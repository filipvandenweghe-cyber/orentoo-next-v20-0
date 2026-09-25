# -*- coding: utf-8 -*-
"""
weather.location — a geographic point for which we track weather forecasts.

Geocoding:
  When the address field changes (create or write), the module automatically
  calls the Nominatim geocoder to populate latitude and longitude.
  Manually entered coordinates are preserved unless the address changes again.

Forecast update:
  _fetch_forecast()  — fetch API + upsert lines for a single location.
  action_update_weather()       — button on the form view.
  action_update_all_weather()   — global action for all active locations.
  _cron_update_weather()        — called by the scheduled cron job.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..services.weather_api import fetch_onecall, build_forecast_lines, geocode_address

_logger = logging.getLogger(__name__)


class WeatherLocation(models.Model):
    _name = 'weather.location'
    _description = 'Weather Location'
    _rec_name = 'name'
    _order = 'name asc'

    # -----------------------------------------------------------------------
    # Fields
    # -----------------------------------------------------------------------
    name = fields.Char(string='Name', required=True)
    address = fields.Char(
        string='Address',
        help='Free-text address.  Saved → geocoded automatically via OpenStreetMap.',
    )
    latitude = fields.Float(
        string='Latitude',
        digits=(10, 6),
        help='Decimal degrees, north positive.  Auto-filled by geocoding.',
    )
    longitude = fields.Float(
        string='Longitude',
        digits=(10, 6),
        help='Decimal degrees, east positive.  Auto-filled by geocoding.',
    )
    active = fields.Boolean(
        string='Active',
        default=True,
        help='Inactive locations are excluded from the scheduled cron update.',
    )
    weather_line_ids = fields.One2many(
        comodel_name='weather.forecast.line',
        inverse_name='location_id',
        string='Forecast Lines',
    )
    last_weather_update = fields.Datetime(
        string='Last Updated',
        compute='_compute_last_weather_update',
        store=True,
        help='UTC timestamp of the most recent API fetch that updated this location\'s forecast lines.',
    )

    @api.depends('weather_line_ids.last_api_update_datetime')
    def _compute_last_weather_update(self):
        for record in self:
            datetimes = record.weather_line_ids.mapped('last_api_update_datetime')
            record.last_weather_update = max(datetimes) if datetimes else False

    # -----------------------------------------------------------------------
    # Geocoding — triggered on create / address change
    # -----------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        """Auto-geocode on create when an address is provided but no coordinates."""
        records = super().create(vals_list)
        for record in records:
            if record.address and not (record.latitude or record.longitude):
                record._geocode_address()
        return records

    def write(self, vals):
        """Re-geocode when the address field is explicitly changed."""
        address_changed = 'address' in vals
        result = super().write(vals)
        if address_changed:
            for record in self:
                if record.address:
                    record._geocode_address()
        return result

    def _geocode_address(self):
        """
        Geocode self.address using Nominatim and write back lat/lon.

        Raises UserError with a clear message when geocoding fails so the
        user immediately knows the problem without inspecting server logs.
        """
        if not self.address:
            return
        try:
            lat, lon = geocode_address(self.address)
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        except Exception as exc:
            raise UserError(
                _(
                    'Geocoding failed for "%(address)s": %(error)s\n\n'
                    'You can enter the coordinates manually.',
                    address=self.address,
                    error=str(exc),
                )
            ) from exc

        # Use sudo() to bypass any potential write-access restriction on the
        # location record during create/write hooks.
        self.sudo().write({'latitude': lat, 'longitude': lon})

    # -----------------------------------------------------------------------
    # Forecast update — public entry points
    # -----------------------------------------------------------------------

    def action_update_weather(self):
        """
        Button action on the location form: fetch and store the forecast.
        Returns a client-side notification with the number of upserted lines.
        """
        self.ensure_one()
        lines_count = self._fetch_forecast()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Weather Updated'),
                'message': _(
                    'Forecast for %(name)s updated: %(count)d lines created/updated.',
                    name=self.name,
                    count=lines_count,
                ),
                'type': 'success',
                'sticky': False,
            },
        }

    @api.model
    def action_update_all_weather(self):
        """
        Global action (accessible from the menu / action button) that updates
        all active locations.  Intended for on-demand testing.
        """
        locations = self.search([('active', '=', True)])
        if not locations:
            raise UserError(_('No active weather locations found.'))

        total_lines = 0
        errors = []
        for loc in locations:
            try:
                total_lines += loc._fetch_forecast()
            except Exception as exc:
                errors.append(f'{loc.name}: {exc}')
                _logger.error('Weather update failed for %s: %s', loc.name, exc)

        message = _(
            'Updated %(lines)d forecast lines for %(locs)d location(s).',
            lines=total_lines,
            locs=len(locations) - len(errors),
        )
        if errors:
            message += '\n\n' + _('Errors:\n') + '\n'.join(errors)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Weather Update Complete'),
                'message': message,
                'type': 'warning' if errors else 'success',
                'sticky': bool(errors),
            },
        }

    @api.model
    def _cron_update_weather(self):
        """
        Entry point for the daily scheduled cron job.
        Updates all active locations; failures are logged but do not abort
        the remaining locations so one bad location never blocks the others.
        """
        locations = self.search([('active', '=', True)])
        _logger.info('Weather cron: updating %d active location(s).', len(locations))
        for loc in locations:
            try:
                count = loc._fetch_forecast()
                _logger.info('Weather cron: %s → %d lines upserted.', loc.name, count)
            except Exception as exc:
                _logger.error(
                    'Weather cron: update failed for location "%s" (id=%d): %s',
                    loc.name, loc.id, exc,
                )

    # -----------------------------------------------------------------------
    # Internal forecast fetch + upsert
    # -----------------------------------------------------------------------

    def _fetch_forecast(self):
        """
        Fetch weather forecast from OpenWeather One Call API 3.0 and upsert
        the resulting hourly lines into weather.forecast.line.

        Returns:
            int — total number of lines created or updated.

        Raises:
            UserError — for misconfiguration or API errors; safe to surface to the UI.
        """
        self.ensure_one()

        # --- Validate prerequisites ---
        ICP = self.env['ir.config_parameter'].sudo()
        api_key = ICP.get_str('weather_forecast.api_key', '').strip()
        units = ICP.get_str('weather_forecast.units', 'metric')
        lang = ICP.get_str('weather_forecast.language', 'en')

        if not api_key:
            raise UserError(
                _(
                    'OpenWeather API key is not configured.  '
                    'Please go to Settings → Weather Forecast and enter your API key.'
                )
            )
        if not self.latitude and not self.longitude:
            raise UserError(
                _(
                    'Location "%(name)s" has no coordinates.  '
                    'Please set the address so it can be geocoded, '
                    'or enter latitude/longitude manually.',
                    name=self.name,
                )
            )

        # --- Call API (raises ValueError / requests.RequestException on failure) ---
        try:
            data = fetch_onecall(self.latitude, self.longitude, api_key, units, lang)
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        except Exception as exc:
            raise UserError(
                _('API request failed for "%(name)s": %(error)s', name=self.name, error=str(exc))
            ) from exc

        # --- Transform API response to forecast line dicts ---
        lines_data = build_forecast_lines(data)

        # --- Upsert into weather.forecast.line ---
        return self._upsert_forecast_lines(lines_data)

    def _upsert_forecast_lines(self, lines_data):
        """
        Batch upsert a list of forecast-line dicts for this location.

        Update rules (per spec):
          - Only overwrite a field when the new API response contains a value.
          - Do not delete historical records outside the new forecast window.
          - Do not create a record with an explicit NULL for missing values;
            simply omit the key so the ORM leaves the column at its default.

        Uses a single bulk-read to find all existing records in the target
        datetime range, then splits into creates and updates for efficiency.

        Returns:
            int — total records created + updated.
        """
        if not lines_data:
            return 0

        ForecastLine = self.env['weather.forecast.line'].sudo()

        dts = [ln['forecast_datetime'] for ln in lines_data]
        min_dt, max_dt = min(dts), max(dts)

        # One query to fetch all existing lines in the forecast window.
        existing_records = ForecastLine.search([
            ('location_id', '=', self.id),
            ('forecast_datetime', '>=', min_dt),
            ('forecast_datetime', '<=', max_dt),
        ])
        existing_by_dt = {rec.forecast_datetime: rec for rec in existing_records}

        to_create = []
        updates = []  # list of (record, vals_dict)

        for ln in lines_data:
            dt = ln['forecast_datetime']
            # Strip None values — spec says "leave existing value unchanged"
            # when the API does not return a value.
            non_null = {k: v for k, v in ln.items() if v is not None}

            existing = existing_by_dt.get(dt)
            if existing:
                updates.append((existing, non_null))
            else:
                create_vals = dict(non_null)
                create_vals['location_id'] = self.id
                to_create.append(create_vals)

        created_count = 0
        if to_create:
            ForecastLine.create(to_create)
            created_count = len(to_create)

        for rec, vals in updates:
            rec.write(vals)

        total = created_count + len(updates)
        _logger.info(
            '_upsert_forecast_lines for "%s": %d created, %d updated.',
            self.name, created_count, len(updates),
        )
        return total
