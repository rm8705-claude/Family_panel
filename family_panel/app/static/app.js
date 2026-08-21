/* ===========================================================================
   Family Panel — single-page frontend (vanilla JS, no build step).

   Codes strictly to API.md. Everything the API returns is already local
   (Australia/Brisbane) — we never do timezone maths here, we just format.
   "Now" comes from the device clock.

   Demo/integration mode: append ?mock=1 to serve every endpoint from the
   fixture layer at the bottom of this file. Completely inert otherwise.
   =========================================================================== */
(function () {
  'use strict';

  var Q = new URLSearchParams(location.search);
  var MOCK = location.search.includes('mock=1');
  var FORCE_SLEEP = Q.get('sleep') === '1';

  /* ----------------------------------------------------------- utilities - */

  function pad2(n) { return (n < 10 ? '0' : '') + n; }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function ymd(d) {
    return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
  }

  function todayStr() { return ymd(new Date()); }

  function addDays(s, n) {
    var p = s.split('-').map(Number);
    var d = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
    d.setUTCDate(d.getUTCDate() + n);
    return d.toISOString().slice(0, 10);
  }

  function dowOf(s) {                       // 0 = Sunday
    var p = s.split('-').map(Number);
    return new Date(Date.UTC(p[0], p[1] - 1, p[2])).getUTCDay();
  }

  function mondayOf(s) { return addDays(s, -((dowOf(s) + 6) % 7)); }

  function diffDays(a, b) {
    var pa = a.split('-').map(Number), pb = b.split('-').map(Number);
    return Math.round((Date.UTC(pb[0], pb[1] - 1, pb[2]) - Date.UTC(pa[0], pa[1] - 1, pa[2])) / 86400000);
  }

  var DOW_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var DOW_CODE = ['SU', 'MO', 'TU', 'WE', 'TH', 'FR', 'SA'];
  var MON_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  var MON_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
    'August', 'September', 'October', 'November', 'December'];

  function fmtDMY(s) { var p = s.split('-'); return p[2] + '/' + p[1] + '/' + p[0]; }

  function fmtLongDate(s) {
    var p = s.split('-').map(Number);
    return DOW_SHORT[dowOf(s)] + ' ' + p[2] + ' ' + MON_LONG[p[1] - 1];
  }

  function fmtTime(t) {                     // "07:30" -> "7:30 am"
    if (!t) return '';
    var p = t.split(':').map(Number);
    var ap = p[0] < 12 ? 'am' : 'pm';
    var h = p[0] % 12 === 0 ? 12 : p[0] % 12;
    return h + ':' + pad2(p[1]) + ' ' + ap;
  }

  function fmtTimeShort(t) {                // "07:30" -> "7:30a"
    if (!t) return '';
    var p = t.split(':').map(Number);
    var ap = p[0] < 12 ? 'a' : 'p';
    var h = p[0] % 12 === 0 ? 12 : p[0] % 12;
    return h + (p[1] ? ':' + pad2(p[1]) : '') + ap;
  }

  function mins(t) { if (!t) return 0; var p = t.split(':').map(Number); return p[0] * 60 + p[1]; }

  function nowMins() { var d = new Date(); return d.getHours() * 60 + d.getMinutes(); }

  function clockNow() {
    var d = new Date();
    return fmtTime(pad2(d.getHours()) + ':' + pad2(d.getMinutes()));
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : many); }

  /* Debug-footer timestamps: render whatever the API gives us in house style,
     falling back to the raw string if it is not a date we can parse. */
  function fmtStamp(s) {
    if (!s) return '—';
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(s);
    return fmtDMY(ymd(d)) + ' ' + fmtTime(pad2(d.getHours()) + ':' + pad2(d.getMinutes()));
  }

  /* "4 mins ago" — the sync surfaces read as human time, not timestamps. */
  function relTime(s) {
    if (!s) return 'never';
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(s);
    var secs = Math.round((Date.now() - d.getTime()) / 1000);
    if (secs < 0) secs = 0;
    if (secs < 45) return 'just now';
    var m = Math.round(secs / 60);
    if (m < 60) return plural(m, 'min', 'mins') + ' ago';
    var h = Math.round(m / 60);
    if (h < 24) return plural(h, 'hour', 'hours') + ' ago';
    return plural(Math.round(h / 24), 'day', 'days') + ' ago';
  }

  function byId(id) { return document.getElementById(id); }

  /* ---------------------------------------------------------- colour maths */
  /* The four family accents come from /api/status, so every tint has to be
     derived at runtime rather than written into the stylesheet. Night mode
     lifts each accent ~15-20% toward white so it survives a charcoal ground. */

  function hex2rgb(h) {
    h = String(h == null ? '' : h).trim().replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    if (h.length !== 6 || isNaN(n)) return [142, 145, 153];
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  function rgb2hex(c) {
    return '#' + c.map(function (v) {
      var s = Math.max(0, Math.min(255, Math.round(v))).toString(16);
      return s.length < 2 ? '0' + s : s;
    }).join('');
  }

  /* mixHex(a, b, t) — t of the way from a to b */
  function mixHex(a, b, t) {
    var x = hex2rgb(a), y = hex2rgb(b);
    return rgb2hex([0, 1, 2].map(function (i) { return x[i] + (y[i] - x[i]) * t; }));
  }

  function relLum(h) {
    var c = hex2rgb(h).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }

  /* ---------------------------------------------------------------- skins - */
  /* A skin is a full replacement of both token sets in styles.css. This table
     is the JS half of the same palette: the handful of values the accent maths
     needs, per skin per theme, and they MUST match the CSS blocks or the
     tinted pills will sit a shade off their own cards.

       card    the surface tints are mixed onto
       ink     the type colour on that surface
       chrome  the page ground (meta theme-color + the pre-paint background)
       hero    the signature colour — only used to draw the picker swatch
       tint    how much accent goes into a wash
       lift    how far an accent is pulled toward white before use
       dark    true when the surface is dark, so accents are used neat

     `dark` is what lets Slate work as a DAY theme: the accent handling keys
     off how light the card is, not off which theme is running. */

  var SKINS = [
    {
      id: 'paper', name: 'Warm paper', note: 'The original stock',
      day:   { card: '#FDFBF5', ink: '#1F2228', chrome: '#EFE9DD', hero: '#9E4B2C', tint: 0.13, lift: 0,    dark: false },
      night: { card: '#1E212A', ink: '#F2EDE3', chrome: '#111319', hero: '#E9A05A', tint: 0.20, lift: 0.20, dark: true }
    },
    {
      id: 'slate', name: 'Slate', note: 'Charcoal wallboard',
      day:   { card: '#2C3037', ink: '#F1EEE7', chrome: '#23262B', hero: '#E39A57', tint: 0.22, lift: 0.22, dark: true },
      night: { card: '#1C1F25', ink: '#EAE6DD', chrome: '#15171B', hero: '#DB9152', tint: 0.22, lift: 0.22, dark: true }
    },
    {
      id: 'eucalypt', name: 'Eucalypt', note: 'Sage and gum leaf',
      day:   { card: '#FAFBF3', ink: '#1C231D', chrome: '#E4E7DB', hero: '#3F6B4A', tint: 0.13, lift: 0,    dark: false },
      night: { card: '#1C211A', ink: '#EDF0E3', chrome: '#12160F', hero: '#86B87F', tint: 0.20, lift: 0.20, dark: true }
    },
    {
      id: 'coast', name: 'Coast', note: 'Sand and ocean',
      day:   { card: '#FDFAF2', ink: '#1D2A33', chrome: '#EEE5D3', hero: '#0F6478', tint: 0.13, lift: 0,    dark: false },
      night: { card: '#17222B', ink: '#EAF1F3', chrome: '#0E161C', hero: '#5FB0CE', tint: 0.20, lift: 0.20, dark: true }
    },
    {
      id: 'ink', name: 'Ink', note: 'Gallery white, one accent',
      day:   { card: '#FFFFFF', ink: '#0B0B0D', chrome: '#F1F1EE', hero: '#9E4B2C', tint: 0.12, lift: 0,    dark: false },
      night: { card: '#17171A', ink: '#F4F4F1', chrome: '#0B0B0D', hero: '#E0824F', tint: 0.20, lift: 0.20, dark: true }
    }
  ];

  var SKIN_KEY = 'panel-skin';

  function skinDef(id) {
    for (var i = 0; i < SKINS.length; i++) if (SKINS[i].id === id) return SKINS[i];
    return SKINS[0];
  }

  /* The surface pair for a named skin + theme. */
  function surfaceOf(skin, theme) {
    return skinDef(skin)[theme === 'night' ? 'night' : 'day'];
  }

  /* The surface actually on screen right now — the one every accent helper
     below asks for. */
  function surf() { return surfaceOf(state.skin, state.theme); }

  /* --------------------------------------------------------------- icons - */

  function svg(inner, extra) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" ' +
      'stroke-linecap="round" stroke-linejoin="round" ' + (extra || '') + '>' + inner + '</svg>';
  }

  var ICON = {
    tick: svg('<path d="M4.5 12.5l5 5 10-11"/>', 'stroke-width="2.6"'),
    star: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M12 2.6l2.9 6 6.6.9-4.8 4.6 1.2 6.5L12 17.5 6.1 20.6l1.2-6.5L2.5 9.5l6.6-.9z"/></svg>',
    left: svg('<path d="M15 5l-7 7 7 7"/>'),
    right: svg('<path d="M9 5l7 7-7 7"/>'),
    close: svg('<path d="M6 6l12 12M18 6L6 18"/>'),
    plus: svg('<path d="M12 5v14M5 12h14"/>'),
    edit: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/>'),
    trash: svg('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
    pin: svg('<rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 018 0v3"/>'),
    play: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none"><path d="M7.5 4.8v14.4L19.2 12z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">' +
      '<rect x="6.5" y="5" width="4" height="14" rx="1.2"/><rect x="13.5" y="5" width="4" height="14" rx="1.2"/></svg>',
    prev: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">' +
      '<path d="M19.5 5.4v13.2L9.6 12z"/><rect x="4.8" y="5.2" width="3.2" height="13.6" rx="1.4"/></svg>',
    next: '<svg viewBox="0 0 24 24" fill="currentColor" stroke="none">' +
      '<path d="M4.5 5.4v13.2L14.4 12z"/><rect x="16" y="5.2" width="3.2" height="13.6" rx="1.4"/></svg>',
    speaker: svg('<rect x="4" y="2.5" width="16" height="19" rx="3"/><circle cx="12" cy="15" r="4"/>' +
      '<circle cx="12" cy="7" r="1.6"/>'),
    radio: svg('<rect x="2.5" y="8.5" width="19" height="11.5" rx="2.5"/><path d="M8 8.5l10.5-4.2"/>' +
      '<circle cx="16" cy="14.2" r="2.8"/><path d="M6.5 12.4v3.6"/>'),
    playlist: svg('<path d="M3.5 6.5h11M3.5 11.5h11M3.5 16.5h6.5"/><circle cx="17.5" cy="16.8" r="2.6"/>' +
      '<path d="M20.1 16.8V8.4l1.9.8"/>'),
    dots: svg('<circle cx="5" cy="12" r="1.5" fill="currentColor" stroke="none"/>' +
      '<circle cx="12" cy="12" r="1.5" fill="currentColor" stroke="none"/>' +
      '<circle cx="19" cy="12" r="1.5" fill="currentColor" stroke="none"/>'),
    /* public holidays: a small pennant, never a person's colour */
    flag: svg('<path d="M6.5 21.5V3.2"/><path d="M6.5 4.2h11l-2.1 3.6 2.1 3.6h-11z"/>'),
    mic: svg('<rect x="9" y="2.5" width="6" height="11.5" rx="3"/>' +
      '<path d="M5.5 11.5a6.5 6.5 0 0013 0"/><path d="M12 18v3.5"/>')
  };

  /* The power-flow family. These are kept as bare path markup rather than
     finished <svg>s because the overlay draws the same four glyphs again,
     scaled up, inside the diagram's own coordinate system. */
  var GLYPH = {
    sun: '<circle cx="12" cy="12" r="4.6"/>' +
      '<path d="M12 2.2v2.4M12 19.4v2.4M2.2 12h2.4M19.4 12h2.4' +
      'M5.1 5.1l1.7 1.7M17.2 17.2l1.7 1.7M18.9 5.1l-1.7 1.7M6.8 17.2l-1.7 1.7"/>',
    house: '<path d="M3.2 11L12 3.4l8.8 7.6"/><path d="M5.6 9.6V20.6h12.8V9.6"/>' +
      '<path d="M9.9 20.6v-5.4h4.2v5.4"/>',
    /* a transmission pylon: two splayed legs, cross-brace, cross-arm */
    pylon: '<path d="M6.8 21L10.9 3.4h2.2L17.2 21"/><path d="M9.3 11.6h5.4M8.2 16.2h7.6"/>' +
      '<path d="M6.2 7h11.6"/><path d="M10.1 7l3.8 4.6M13.9 7l-3.8 4.6"/>'
  };
  ICON.sun = svg(GLYPH.sun);
  /* solid triangles: charge up / discharge down on the battery pill */
  ICON.up = '<svg viewBox="0 0 12 12" fill="currentColor" stroke="none"><path d="M6 2.2L10.4 9.4H1.6z"/></svg>';
  ICON.down = '<svg viewBox="0 0 12 12" fill="currentColor" stroke="none"><path d="M6 9.8L1.6 2.6h8.8z"/></svg>';

  /* Weather — WMO code to glyph + description */

  function wmoGroup(code) {
    if (code === 0) return 'clear';
    if (code === 1) return 'mostly';
    if (code === 2) return 'partly';
    if (code === 3) return 'cloud';
    if (code === 45 || code === 48) return 'fog';
    if (code >= 51 && code <= 57) return 'drizzle';
    if (code >= 61 && code <= 67) return 'rain';
    if (code >= 71 && code <= 77) return 'snow';
    if (code >= 80 && code <= 82) return 'showers';
    if (code === 85 || code === 86) return 'snow';
    if (code >= 95) return 'storm';
    return 'cloud';
  }

  var WMO_TEXT = {
    clear: 'Clear', mostly: 'Mostly clear', partly: 'Partly cloudy', cloud: 'Overcast',
    fog: 'Fog', drizzle: 'Drizzle', rain: 'Rain', showers: 'Showers',
    storm: 'Thunderstorms', snow: 'Snow'
  };

  var CLOUD = '<path d="M7.5 18.5h9.2a3.6 3.6 0 00.3-7.2 5.2 5.2 0 00-9.9-1.3 3.8 3.8 0 00.4 8.5z"/>';

  function wxGlyph(code) {
    var g = wmoGroup(code);
    if (g === 'clear' || g === 'mostly') {
      return svg('<circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2' +
        'M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6"/>');
    }
    if (g === 'partly') {
      return svg('<circle cx="8.6" cy="8.4" r="3"/><path d="M8.6 2.9v1.6M3.1 8.4h1.6M4.7 4.5l1.1 1.1M12.5 4.5l-1.1 1.1"/>' +
        '<path d="M9.4 19.5h8a3.3 3.3 0 00.3-6.6 4.8 4.8 0 00-9.1-1.2 3.5 3.5 0 00.8 7.8z"/>');
    }
    if (g === 'fog') return svg(CLOUD + '<path d="M4 21h9M15.5 21h4.5"/>');
    if (g === 'drizzle') return svg(CLOUD + '<path d="M9.5 21v1M13 20.6v1.4M16.5 21v1"/>');
    if (g === 'rain' || g === 'showers') return svg(CLOUD + '<path d="M9 20.2l-.8 2.2M13 20.2l-.8 2.2M17 20.2l-.8 2.2"/>');
    if (g === 'storm') return svg(CLOUD + '<path d="M13 20l-3.2 2.4h3l-1 2"/>');
    if (g === 'snow') return svg(CLOUD + '<path d="M9 21.4h.01M13 21.9h.01M17 21.4h.01"/>', 'stroke-width="2.4"');
    return svg(CLOUD);
  }

  function wxText(code, fallback) {
    return fallback || WMO_TEXT[wmoGroup(code)] || 'Unknown';
  }

  /* ------------------------------------------------------------ api layer - */

  var state = {
    view: 'today',
    cursor: todayStr(),         // day/week/month/meals navigation anchor
    today: todayStr(),
    pin: '',
    online: true,
    lastGoodAt: null,
    rolling: false,             // a midnight rollover is waiting on its agenda
    showOtherMeals: false,
    booted: false,
    wakeUntil: 0,
    np: null,                   // entity of the open now-playing overlay
    npStart: null,              // {entity, name, at} — favourite we just asked for
    climate: false,             // the whole-house aircon sheet is open
    camIdx: 0,                  // which channel the grouped Cameras tile shows
    solar: false,               // the power-flow overlay is open
    wgScrolled: false,          // the week grid has found its scroll position
    theme: 'day',               // the theme actually applied right now
    themeMode: 'auto',          // 'auto' | 'day' | 'night'
    skin: 'paper'               // which material the panel is wearing
  };

  var D = {
    status: null, agenda: null, chores: null, imports: null, ha: null,
    weather: null, lists: null, recipes: null, mealplan: null, stars: null,
    photos: null,
    byDate: {}, agendaFrom: null, agendaTo: null
  };

  /* A wall panel on house Wi-Fi loses the backend without being told: the
     socket just sits there. Without a deadline the pollers stack requests
     while the link is down and then all land at once when it comes back, so
     every request carries its own abort timer. */
  var API_TIMEOUT_MS = 10000;
  var API_AUDIO_TIMEOUT_MS = 35000;

  /* Arm an abort timer on a fetch options object. Returns a disarm function —
     always call it, on both arms of the promise. */
  function armTimeout(opts, ms) {
    if (!window.AbortController) return function () { };
    var ctl = new AbortController();
    opts.signal = ctl.signal;
    var timer = setTimeout(function () {
      try { ctl.abort(); } catch (e) { /* already settled */ }
    }, ms);
    return function () { clearTimeout(timer); };
  }

  function timedOut(err) {
    var e = new Error('The panel timed out reaching the backend.');
    e.timeout = true;
    if (!(err && err.name === 'AbortError')) return err;
    return e;
  }

  function api(method, path, body) {
    if (window.__mock && window.__mock.enabled) return mockRequest(method, path, body);
    var opts = { method: method, headers: {}, cache: 'no-store' };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    if (state.pin) opts.headers['X-Admin-Pin'] = state.pin;
    var disarm = armTimeout(opts, API_TIMEOUT_MS);
    return fetch(path, opts).then(function (res) {
      disarm();
      if (!res.ok) {
        return res.json().catch(function () { return null; }).then(function (d) {
          var err = new Error((d && d.error) || ('Request failed (' + res.status + ')'));
          err.status = res.status;
          throw err;
        });
      }
      return res.json();
    }, function (err) {
      disarm();
      throw timedOut(err);
    });
  }

  /* One poll of a given kind at a time. A slow or hung request must not let
     the next beat of the same timer queue a second one behind it — that is
     how a thirty-second dropout turns into a burst of eight renders. */
  var POLLING = {};

  /* Only the timer callbacks go through this — a refetch that follows a tap
     must never be swallowed, so user-initiated loads call the loaders direct. */
  function solo(key, fn) {
    if (POLLING[key]) return Promise.resolve(null);
    POLLING[key] = true;
    return fn().then(function (d) {
      delete POLLING[key];
      return d;
    }, function (err) {
      delete POLLING[key];
      throw err;
    });
  }

  var GET = function (p) { return api('GET', p); };
  var POST = function (p, b) { return api('POST', p, b === undefined ? {} : b); };
  var PATCH = function (p, b) { return api('PATCH', p, b); };
  var PUT = function (p, b) { return api('PUT', p, b); };
  var DEL = function (p) { return api('DELETE', p); };

  function markOnline() { state.online = true; state.lastGoodAt = new Date(); }

  /* Only a genuine dropout (no HTTP response at all) counts as offline —
     a 4xx means the panel reached the backend and got an answer. */
  function markOffline(err) {
    if (err && err.status !== undefined) return;
    state.online = false;
  }

  /* Wrap a background load so a failure never surfaces as an unhandled
     rejection and never wipes cached data. */
  function bg(promise, onOk) {
    return promise.then(function (d) {
      markOnline();
      if (onOk) onOk(d);
      renderSyncNote();
      return d;
    }, function (err) {
      markOffline(err);
      renderSyncNote();
      return null;
    });
  }

  /* Wrap a user-initiated action: report failures as a toast. */
  function act(promise, onOk) {
    return promise.then(function (d) {
      markOnline();
      if (onOk) onOk(d);
      return d;
    }, function (err) {
      markOffline(err);
      /* A 403 about the PIN means the one we hold is wrong — drop it and ask
         again with the pad shaking. Other 403s (a read-only HA tile) just say
         what happened. */
      if (err && err.status === 403 && /pin/i.test(err.message || '')) {
        state.pin = '';
        openPin('That PIN didn’t work — try again.', { shake: true });
      } else if (err && err.status === 403) {
        toast(err.message || 'That one is read-only.');
      } else {
        toast((err && err.message) || 'Could not reach the panel. It will retry.');
      }
      renderSyncNote();
      return null;
    });
  }

  /* ------------------------------------------------------ self-recovery --- */
  /* Nobody is going to walk over and pull the plug. One uncaught throw inside
     a view builder used to leave the wall frozen on whatever it last painted,
     with the clock stopped, for as long as it took somebody to notice. So the
     panel watches itself: three failures in a row, or twenty minutes without a
     single good answer from the backend, and it reloads.

     Never in the demo — the artifact has no backend to be stale against — and
     never inside the first two minutes of a load, so a fault that survives the
     reload cannot turn into a reload loop. */

  var BOOT_AT = Date.now();
  var RELOAD_FLOOR_MS = 120000;
  var STALE_MS = 20 * 60000;
  var failStreak = 0;

  function hardReload(why) {
    if (MOCK) return false;
    if (Date.now() - BOOT_AT < RELOAD_FLOOR_MS) return false;
    try { console.warn('panel reloading:', why); } catch (e) { }
    try { location.reload(); } catch (e) { }
    return true;
  }

  function panelFailure(where, err) {
    failStreak++;
    try { console.warn('panel failure in ' + where + ':', (err && (err.message || err)) || err); } catch (e) { }
    if (failStreak >= 3) { failStreak = 0; hardReload(where); }
  }

  /* Anything that got as far as throwing past every handler counts. Nothing is
     swallowed — the browser still logs it, this only keeps the tally. */
  window.onerror = function (msg, src, line, col, err) {
    panelFailure('script', err || msg);
  };
  window.addEventListener('unhandledrejection', function (e) {
    panelFailure('promise', e && e.reason);
  });

  /* Liveness: the pollers keep marking the panel online, so a lastGoodAt that
     has not moved in twenty minutes means the page itself has stopped asking. */
  function watchdog() {
    var last = state.lastGoodAt ? state.lastGoodAt.getTime() : BOOT_AT;
    if (Date.now() - last > STALE_MS) hardReload('stale');
  }

  /* ------------------------------------------------------------- loaders - */

  function neededRange(today, cursor) {
    today = today || state.today;
    cursor = cursor || state.cursor;
    var from = addDays(today, -1);
    var to = addDays(today, 45);
    var a, b;
    if (state.view === 'week' || state.view === 'meals') {
      a = mondayOf(cursor); b = addDays(a, 6);
    } else if (state.view === 'month') {
      var p = cursor.split('-').map(Number);
      var first = p[0] + '-' + pad2(p[1]) + '-01';
      a = mondayOf(first); b = addDays(a, 41);
    } else if (state.view === 'day') {
      a = cursor; b = cursor;
    }
    if (a && a < from) from = a;
    if (b && b > to) to = b;
    return [from, to];
  }

  function indexAgenda() {
    var map = {};
    var evs = (D.agenda && D.agenda.events) || [];
    evs.forEach(function (e) {
      var d = e.date, end = e.end_date || e.date, guard = 0;
      while (d <= end && guard++ < 90) {
        (map[d] = map[d] || []).push(e);
        d = addDays(d, 1);
      }
    });
    Object.keys(map).forEach(function (k) { map[k].sort(evCmp); });
    D.byDate = map;
  }

  function evCmp(a, b) {
    if (a.all_day !== b.all_day) return a.all_day ? -1 : 1;
    if (a.all_day) return (a.title || '').localeCompare(b.title || '');
    var d = mins(a.start) - mins(b.start);
    return d || (a.title || '').localeCompare(b.title || '');
  }

  /* The un-guarded fetch, so the midnight rollover can ask for the new day's
     range without being swallowed by a poll that happens to be in flight. */
  function fetchAgenda(r) {
    return bg(GET('/api/agenda?from=' + r[0] + '&to=' + r[1]), function (d) {
      D.agenda = d; D.agendaFrom = r[0]; D.agendaTo = r[1];
      indexAgenda();
    });
  }

  function loadAgenda() {
    return fetchAgenda(neededRange());
  }

  function loadStatus() {
    return bg(GET('/api/status'), function (d) { D.status = d; });
  }

  function loadChores() {
    return bg(GET('/api/chores/today?date=' + state.today), function (d) { D.chores = d; });
  }

  function loadImports() {
    return bg(GET('/api/imports/pending'), function (d) { D.imports = d; });
  }

  /* How many HA polls in a row have come back empty-handed. The 15 s beat
     backs off against this rather than hammering a house that is off. */
  var haFail = 0;

  function loadHa() {
    return GET('/api/ha/tiles').then(function (d) {
      markOnline(); D.ha = d; haFail = 0; renderSyncNote();
      return d;
    }, function (err) {
      markOffline(err); haFail++; renderSyncNote();
      return null;
    });
  }

  function loadWeather() {
    return bg(GET('/api/weather'), function (d) { D.weather = d; });
  }

  function loadPhotos() {
    return bg(GET('/api/photos'), function (d) { D.photos = d; });
  }

  function loadMealplan(week) {
    return bg(GET('/api/mealplan?week=' + (week || mealAnchor())), function (d) { D.mealplan = d; });
  }

  /* Which week the meal plan should be loaded for, given the current view. */
  function mealAnchor() {
    return (state.view === 'meals' || state.view === 'week') ? state.cursor : state.today;
  }

  function loadRecipes() {
    return bg(GET('/api/recipes'), function (d) { D.recipes = d; });
  }

  function loadLists() {
    return bg(GET('/api/lists'), function (d) { D.lists = d; });
  }

  function loadStars() {
    return bg(GET('/api/stars'), function (d) { D.stars = d; });
  }

  /* --------------------------------------------------------------- people - */

  function people() {
    return (D.status && D.status.config && D.status.config.people) || [];
  }

  function person(id) {
    var list = people();
    for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
    return null;
  }

  function colourRaw(id) {
    var p = person(id);
    return p ? p.colour : (surf().dark ? '#7E8894' : '#8E9199');
  }

  /* The accent as it should appear on the current skin + theme. */
  function colourOf(id) {
    var base = colourRaw(id);
    var lift = surf().lift;
    return lift ? mixHex(base, '#FFFFFF', lift) : base;
  }

  /* Soft wash of the accent over the card colour — for pills and sections. */
  function tintOf(id) {
    var s = surf();
    return mixHex(s.card, colourOf(id), s.tint);
  }

  /* The accent, made safe to set type in on a card (mango needs darkening on
     light stock; on a dark card the lifted accent is already bright enough,
     which is why this asks the surface and not the theme — Slate is a dark
     card in broad daylight). */
  function inkOf(id) {
    var s = surf();
    var a = colourOf(id);
    if (s.dark) return a;
    return relLum(a) > 0.32 ? mixHex(a, s.ink, 0.42) : a;
  }

  /* What reads on top of a solid accent disc. The threshold sits low so
     mango — bright but not quite "light" by luminance — gets dark ink. */
  function onOf(id) {
    return relLum(colourOf(id)) > 0.36 ? '#1A1C21' : '#FFFFFF';
  }

  /* Every custom property a person-coloured element wants. */
  function pstyle(id) {
    var s = surf();
    var c = colourOf(id);
    return '--c:' + c + ';--c-tint:' + tintOf(id) +
      ';--c-wash:' + mixHex(s.card, c, s.tint * 0.45) +
      ';--c-ink:' + inkOf(id) + ';--c-on:' + onOf(id) +
      ';--c-line:' + mixHex(s.card, c, 0.38);
  }

  function nameOf(id) {
    var p = person(id);
    return p ? p.name : '';
  }

  function initialsOf(name) {
    var parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return '?';
    if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  /* person chip: tinted pill + initials disc. `bare` drops the pill. */
  function personChip(id, opts) {
    opts = opts || {};
    var name = nameOf(id);
    if (!name) return '';
    return '<span class="who-chip' + (opts.bare ? ' bare' : '') + '" style="' + pstyle(id) + '">' +
      '<span class="av">' + esc(initialsOf(name)) + '</span>' +
      (opts.initialsOnly ? '' : esc(name)) + '</span>';
  }

  /* --------------------------------------------------------------- theme -- */
  /* Day theme by default; night from today's sunset until sunrise (API.md
     "Theme"), falling back to 18:00–06:00 when weather is unavailable.
     ?theme=night / ?theme=day and window.__theme.set() force it for
     screenshots and console poking. The sleep overlay is separate and sits
     above whichever theme is running. */

  function sunTimes() {
    var w = D.weather;
    var s = (w && w.sun) || null;
    var rise = s && s.sunrise, set = s && s.sunset;
    if (!rise || !set) {
      var d0 = (w && w.daily && w.daily[0]) || null;   // stale cache without sun{}
      if (d0 && d0.sunrise && d0.sunset) { rise = d0.sunrise; set = d0.sunset; }
    }
    return {
      sunrise: rise || '06:00',
      sunset: set || '18:00',
      real: !!(rise && set)
    };
  }

  function autoTheme() {
    var s = sunTimes(), n = nowMins();
    var rise = mins(s.sunrise), set = mins(s.sunset);
    if (set <= rise) return 'day';                     // nonsense data: stay light
    return (n >= set || n < rise) ? 'night' : 'day';
  }

  var themeFadeTimer = null;

  function applyTheme(next, animate) {
    if (next !== 'night') next = 'day';
    var root = document.documentElement;
    var changed = next !== state.theme;
    if (animate && changed && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      root.classList.add('theme-fading');
      clearTimeout(themeFadeTimer);
      themeFadeTimer = setTimeout(function () {
        root.classList.remove('theme-fading');
      }, 1150);
    }
    state.theme = next;
    root.setAttribute('data-theme', next);
    paintChrome();
    if (changed) render();            // every accent is computed per theme
    /* Say it once, only when dusk or dawn actually flipped it live — boot and
       the ?theme= override pass animate:false and stay quiet. */
    if (changed && animate && state.booted && state.themeMode === 'auto') {
      toast(next === 'night'
        ? 'Good evening — night mode on.'
        : 'Good morning — back to the day theme.', 4200);
    }
  }

  function updateTheme(animate) {
    var want = state.themeMode === 'auto' ? autoTheme() : state.themeMode;
    if (want !== state.theme) applyTheme(want, animate !== false);
    renderThemeDebug();
  }

  /* ?debugtheme=1 — a small on-screen readout of every input the day/night
     decision runs on, for diagnosing "it says night but it isn't" on a
     kiosk tablet where there is no realistic way to open devtools. Shows the
     device's own clock, what it thinks sunrise/sunset are and where that
     data came from, and the theme that follows from them — so a wrong
     device clock, stale/missing weather, and an actual logic bug each look
     different at a glance instead of all presenting as "stuck on night". */
  var THEME_DEBUG = Q.get('debugtheme') === '1';
  function renderThemeDebug() {
    if (!THEME_DEBUG) return;
    var el = byId('themeDebug');
    if (!el) {
      el = document.createElement('div');
      el.id = 'themeDebug';
      el.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:99999;' +
        'background:#000;color:#0f0;font:12px/1.5 monospace;padding:.5rem .75rem;' +
        'white-space:pre-wrap;pointer-events:none;';
      document.body.appendChild(el);
    }
    var s = sunTimes(), n = nowMins();
    var d = new Date();
    el.textContent =
      'device clock:  ' + d.toString() + '\n' +
      'device tz:     ' + Intl.DateTimeFormat().resolvedOptions().timeZone + '\n' +
      'nowMins():     ' + n + '  (' + Math.floor(n / 60) + ':' + String(n % 60).padStart(2, '0') + ')\n' +
      'sun source:    ' + (s.real ? 'weather API' : 'FALLBACK (no weather data)') + '\n' +
      'sunrise/set:   ' + s.sunrise + ' / ' + s.sunset + '\n' +
      'D.weather:     ' + (D.weather ? 'loaded' : 'NULL — never fetched or fetch failed') + '\n' +
      'themeMode:     ' + state.themeMode + '\n' +
      'autoTheme():   ' + autoTheme() + '\n' +
      'state.theme:   ' + state.theme + '\n' +
      'data-theme:    ' + document.documentElement.getAttribute('data-theme') + '\n' +
      'data-skin:     ' + document.documentElement.getAttribute('data-skin') +
      '  (a dark SKIN, e.g. Slate/“Charcoal wallboard”, looks like ' +
      'night even when data-theme above says day — these are two separate settings)';
  }

  /* Push the family accents into CSS so the rail strip can use them. */
  /* THE WHO'S-BUSY BAR — the coloured pills down the rail edge.

     One pill per person, its length proportional to how much that person has
     on today. Everyone carries a base share on top of their count, so the
     family always reads as a family and nobody disappears on a quiet day;
     the events are what stretch a pill out. Counts every entry on today's
     agenda assigned to that person, timed or all-day — a camping trip is a
     commitment the same as a swim lesson. Unassigned and holiday-feed events
     belong to nobody, so they don't move the bar. */

  var BUSY_BASE = 0.7;

  /* --fam1..4 are no longer what draws the strip, but the now-playing record
     still cuts its grooves in the family's colours — so keep the tokens in
     step with the configured people (and lifted on a dark surface). */
  function syncFamilyTokens() {
    var list = people();
    var root = document.documentElement;
    var lift = surf().lift;
    var fallback = ['#3D5A80', '#7C5CBF', '#E8A13D', '#4F7B58'];
    for (var i = 0; i < 4; i++) {
      var p = list[i];
      root.style.setProperty('--fam' + (i + 1),
        p ? colourOf(p.id)
          : (lift ? mixHex(fallback[i], '#FFFFFF', lift) : fallback[i]));
    }
  }

  function renderFamilyStrip() {
    syncFamilyTokens();

    var el = byId('famStrip');
    if (!el) return;

    var list = people();
    if (!list.length) { el.innerHTML = ''; return; }

    var counts = {};
    list.forEach(function (p) { counts[p.id] = 0; });
    (D.byDate[state.today] || []).forEach(function (e) {
      if (e.person_id && Object.prototype.hasOwnProperty.call(counts, e.person_id)) {
        counts[e.person_id]++;
      }
    });

    var total = 0;
    list.forEach(function (p) { total += BUSY_BASE + counts[p.id]; });

    var said = [];
    el.innerHTML = list.map(function (p) {
      var n = counts[p.id];
      var pct = ((BUSY_BASE + n) / total) * 100;
      var what = n ? n + (n === 1 ? ' event' : ' events') : 'nothing on';
      said.push(p.name + ' ' + what);
      return '<div class="fam-seg' + (n ? '' : ' is-quiet') + '" style="flex-basis:' +
        pct.toFixed(2) + '%;--c:' + esc(colourOf(p.id)) + ';--on-c:' + esc(onOf(p.id)) +
        '" title="' + esc(p.name + ' — ' + what + ' today') + '">' +
        '<span class="fam-i">' + esc(initialsOf(p.name).slice(0, 1)) + '</span></div>';
    }).join('');

    el.setAttribute('aria-label', 'Today — ' + said.join(', '));
  }

  /* ---------------------------------------------------------------- skin -- */
  /* The skin is a local choice — one panel in the kitchen, one wall, one
     taste — so it lives in localStorage rather than in the config the backend
     owns. index.html reads the same key before the first paint; this is the
     runtime half. Day/night keeps running underneath, unchanged: the skin
     picks the material, dusk still turns the lights off. */

  function readSkin() {
    try {
      var v = localStorage.getItem(SKIN_KEY);
      return v && skinDef(v).id === v ? v : 'paper';
    } catch (e) { return 'paper'; }         // private mode / storage disabled
  }

  function applySkin(id, opts) {
    opts = opts || {};
    var def = skinDef(id);
    var changed = def.id !== state.skin;
    var root = document.documentElement;
    if (changed && opts.animate !== false &&
        !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      root.classList.add('theme-fading');
      clearTimeout(themeFadeTimer);
      themeFadeTimer = setTimeout(function () {
        root.classList.remove('theme-fading');
      }, 1150);
    }
    state.skin = def.id;
    root.setAttribute('data-skin', def.id);
    if (opts.persist !== false) {
      try { localStorage.setItem(SKIN_KEY, def.id); } catch (e) { /* nothing to do */ }
    }
    paintChrome();
    if (changed && opts.render !== false) render();   // every accent is per skin
    return def.id;
  }

  /* meta theme-color + the html background behind the app — both follow the
     skin's ground so the browser chrome and any overscroll match the panel. */
  function paintChrome() {
    var s = surf();
    var mc = document.querySelector('meta[name="theme-color"]');
    if (mc) mc.setAttribute('content', s.chrome);
    var cs = document.querySelector('meta[name="color-scheme"]');
    if (cs) cs.setAttribute('content', s.dark ? 'dark' : 'light');
    document.documentElement.style.background = s.chrome;
  }

  window.__skin = {
    set: function (id) { return applySkin(id, { animate: false }); },
    get: function () { return state.skin; },
    list: function () {
      return SKINS.map(function (s) { return { id: s.id, name: s.name }; });
    }
  };

  window.__theme = {
    set: function (mode) {
      state.themeMode = (mode === 'day' || mode === 'night') ? mode : 'auto';
      updateTheme(true);
      return state.theme;
    },
    get: function () { return { mode: state.themeMode, theme: state.theme, sun: sunTimes() }; }
  };

  /* ---------------------------------------------------------------- toast - */

  function toast(msg, ms) {
    var host = byId('toastHost');
    var el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    host.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, ms || 3200);
  }

  /* ------------------------------------------------------------- the rail - */

  var TABS = [
    { id: 'today', label: 'Today' },
    { id: 'day', label: 'Day' },
    { id: 'week', label: 'Week' },
    { id: 'month', label: 'Month' },
    { id: 'chores', label: 'Chores' },
    { id: 'lists', label: 'Lists' },
    { id: 'meals', label: 'Meals' }
  ];

  var tabsHtml = null;         // what renderRail last wrote into #tabs

  function renderRail() {
    var t = state.today;
    var p = t.split('-').map(Number);
    byId('railWeekday').textContent = DOW_SHORT[dowOf(t)];
    byId('railDay').textContent = pad2(p[2]);
    byId('railMonth').textContent = MON_SHORT[p[1] - 1];
    byId('railFull').textContent = fmtDMY(t);

    var n = (D.imports && D.imports.imports && D.imports.imports.length) || 0;

    var tabs = byId('tabs');
    var html = TABS.map(function (tab) {
      return '<button class="tab tab-' + tab.id + (state.view === tab.id ? ' is-on' : '') +
        '" data-act="view" data-view="' + tab.id + '">' + tab.label + '</button>';
    }).join('');
    html += '<button class="tab tab-add" data-act="add-event" style="border-color:var(--hair)">' +
      '<span style="display:inline-flex;align-items:center;gap:.35rem">' +
      '<span style="width:1rem;height:1rem;display:inline-block">' + ICON.plus + '</span>Add</span></button>';
    /* Portrait only (CSS hides it in landscape): the drawer that holds Day,
       Month, Add, Magic import and Settings once the rail is a top bar. */
    html += '<button class="tab tab-more" data-act="more">More' +
      (n ? '<span class="tab-badge mono">' + n + '</span>' : '') + '</button>';
    /* Reading .innerHTML back serialises the whole nav to a string on every
       rail repaint — compare against what we last wrote instead. */
    if (tabsHtml !== html) { tabs.innerHTML = html; tabsHtml = html; }

    var btn = byId('btnImports');
    btn.hidden = n === 0;
    byId('importBadge').textContent = n;

    /* Assist only exists when the backend has a Home Assistant to ask — the
       same gate the HA tiles use, read off /api/status. */
    var ab = byId('btnAssist');
    ab.hidden = !assistOn();
    if (!ab.hidden && !byId('btnAssistIc').innerHTML) {
      byId('btnAssistIc').innerHTML = ICON.mic;
    }

    renderSpine();
    renderSyncNote();
    renderFamilyStrip();
  }

  var SPINE_START = 6 * 60, SPINE_END = 22 * 60;

  function spinePct(m) {
    var v = (m - SPINE_START) / (SPINE_END - SPINE_START);
    return Math.max(0, Math.min(1, v)) * 100;
  }

  function renderSpine() {
    var el = byId('spine');
    var now = nowMins();
    var pct = spinePct(now);
    var out = '<div class="spine-line"></div><div class="spine-fill" style="height:' + pct.toFixed(2) + '%"></div>';

    var live = now >= SPINE_START && now <= SPINE_END;
    [[6, '6a'], [9, '9a'], [12, '12p'], [15, '3p'], [18, '6p'], [21, '9p']].forEach(function (t) {
      var p = spinePct(t[0] * 60);
      if (live && Math.abs(p - pct) < 3.5) return;   // don't collide with the now marker
      out += '<div class="spine-tick" style="top:' + p.toFixed(2) + '%">' + t[1] + '</div>';
    });

    var evs = (D.byDate[state.today] || []).filter(function (e) { return !e.all_day && e.start; });
    evs.forEach(function (e) {
      var m = mins(e.start);
      out += '<div class="spine-dot' + (m < now ? ' is-past' : '') + '" style="top:' +
        spinePct(m).toFixed(2) + '%;--c:' + esc(colourOf(e.person_id)) + '"></div>';
    });

    if (live) {
      out += '<div class="spine-now" style="top:' + pct.toFixed(2) + '%"><span class="knob"></span>' +
        '<span class="spine-now-time">' + esc(clockNow()) + '</span></div>';
    }
    el.innerHTML = out;
  }

  function lastSyncLabel() {
    var best = null;
    var jobs = (D.status && D.status.jobs) || {};
    Object.keys(jobs).forEach(function (k) {
      var j = jobs[k];
      if (j && j.at) {
        var d = new Date(j.at);
        if (!isNaN(d) && (!best || d > best)) best = d;
      }
    });
    if (!best) best = state.lastGoodAt;
    if (!best) return null;
    return fmtTime(pad2(best.getHours()) + ':' + pad2(best.getMinutes()));
  }

  function renderSyncNote() {
    var el = byId('syncNote');
    var txt = byId('syncText');
    if (!el || !txt) return;
    var when = lastSyncLabel();
    if (!state.online) {
      el.classList.add('is-stale');
      txt.textContent = when ? 'Offline — last synced ' + when : 'Offline — retrying';
    } else {
      el.classList.remove('is-stale');
      txt.textContent = when ? 'Last synced ' + when : 'Syncing…';
    }
  }

  /* ------------------------------------------------------------ holidays - */
  /* API.md: /api/status `config.feeds` names every ICS feed and its kind.
     A kind:"holiday" feed (the Australian + Queensland public holidays the
     owner asked for) is nobody's diary, so its events never wear a person's
     colour — they read as a quiet hairline chip, sit in the all-day row, and
     are never counted against a month cell's four pills. */

  var feedKindCache = { src: null, map: {} };

  function feedKinds() {
    var list = (D.status && D.status.config && D.status.config.feeds) || null;
    if (feedKindCache.src !== list) {
      var m = {};
      (list || []).forEach(function (f) {
        if (f && f.name) m[f.name] = f.kind || 'calendar';
      });
      feedKindCache = { src: list, map: m };
    }
    return feedKindCache.map;
  }

  function isHoliday(e) {
    return !!(e && e.feed && feedKinds()[e.feed] === 'holiday');
  }

  function splitHolidays(evs) {
    var hol = [], rest = [];
    (evs || []).forEach(function (e) { (isHoliday(e) ? hol : rest).push(e); });
    return { hol: hol, rest: rest };
  }

  function holidaysOn(date) { return (D.byDate[date] || []).filter(isHoliday); }

  /* The chip itself — hairline pill, pennant glyph, no accent anywhere. */
  function holidayChip(e, cls) {
    return '<button class="hol' + (cls ? ' ' + cls : '') + '" data-act="event" data-id="' +
      esc(e.id) + '"><span class="hol-g">' + ICON.flag + '</span>' +
      '<span class="hol-n">' + esc(e.title) + '</span></button>';
  }

  /* Today and Day lead with a quiet full-width strip above the schedule. */
  function holidayStrip(date) {
    var hs = holidaysOn(date);
    if (!hs.length) return '';
    return '<div class="hol-strip">' + hs.map(function (e) {
      return holidayChip(e, 'wide');
    }).join('') + '</div>';
  }

  /* ---------------------------------------------------------- orientation - */
  /* The week view is the one screen whose layout cannot be a media query:
     landscape gets a real time grid, portrait keeps the stacked day cards
     (a 14-hour axis at 390 px is unreadable). Rendering both and hiding one
     doubles the DOM for nothing, so the JS picks — and re-renders when the
     panel is turned. */

  var PORTRAIT_MQ = window.matchMedia
    ? window.matchMedia('(orientation: portrait), (max-width: 700px)') : null;

  function isPortrait() { return !!(PORTRAIT_MQ && PORTRAIT_MQ.matches); }

  /* ------------------------------------------------------- shared pieces - */

  function eventRow(e, opts) {
    opts = opts || {};
    var past = opts.date === state.today && !e.all_day && e.end && mins(e.end) < nowMins();
    var time = e.all_day ? 'All day' : fmtTime(e.start);
    var sub = e.end && !e.all_day ? 'until ' + fmtTime(e.end) : '';
    var bits = [];
    if (e.location) bits.push(esc(e.location));
    if (sub) bits.push(esc(sub));
    return '<button class="ev' + (past ? ' is-past' : '') + (opts.next ? ' is-next' : '') +
      (e.all_day ? ' is-allday' : '') +
      '" data-act="event" data-id="' + esc(e.id) + '" style="' + pstyle(e.person_id) + '">' +
      '<span class="ev-bar"></span>' +
      '<span class="ev-time mono">' + esc(time) + '</span>' +
      '<span class="ev-body"><span class="ev-title">' + esc(e.title) + '</span>' +
      (bits.length ? '<span class="ev-sub">' + bits.join(' · ') + '</span>' : '') + '</span>' +
      '<span class="ev-who">' + personChip(e.person_id) + '</span></button>';
  }

  function scheduleList(date, withNowRule) {
    /* holidays are lifted out into the strip above the list */
    var evs = splitHolidays(D.byDate[date] || []).rest;
    if (!evs.length) {
      return '<div class="empty">Nothing on — enjoy the quiet.</div>';
    }
    var now = nowMins();
    var out = '', ruleDone = !withNowRule || date !== state.today || now < 5 * 60;
    evs.forEach(function (e) {
      /* the row that lands directly under the now line is what is next up */
      var isNext = false;
      if (!ruleDone && !e.all_day && e.start && mins(e.start) > now) {
        out += '<div class="now-rule"><span></span><span class="now-label mono">' + esc(clockNow()) +
          '</span><span class="now-bar"></span></div>';
        ruleDone = true;
        isNext = true;
      }
      out += eventRow(e, { date: date, next: isNext });
    });
    if (!ruleDone) {
      out += '<div class="now-rule"><span></span><span class="now-label mono">' + esc(clockNow()) +
        '</span><span class="now-bar"></span></div>';
    }
    return out;
  }

  function countdownChips() {
    var evs = (D.agenda && D.agenda.events) || [];
    var picks = evs.filter(function (e) {
      return e.countdown && e.date >= state.today;
    }).sort(function (a, b) { return a.date < b.date ? -1 : 1; }).slice(0, 3);
    if (!picks.length) return '';
    return '<div class="chips">' + picks.map(function (e) {
      var n = diffDays(state.today, e.date);
      var label = n === 0 ? 'today' : (n === 1 ? '1 sleep' : n + ' sleeps');
      return '<span class="chip">' + esc(e.title) + ' <b>' + esc(label) + '</b></span>';
    }).join('') + '</div>';
  }

  /* ------------------------------------------------------------ today view */

  function viewToday() {
    /* .pd-* are the portrait-only header pieces: the rail's date block
       shrinks to a strip at the top of the phone, so Today carries the full
       date and the countdown chips itself. Both are display:none in
       landscape, where the rail and the schedule card already hold them. */
    var chips = countdownChips();
    return '<div class="today">' +
      '<div class="pd-strip"><span class="pd-dow">' + esc(fmtLongDate(state.today)) + '</span>' +
      '<span class="pd-date mono">' + esc(fmtDMY(state.today)) + '</span></div>' +
      (chips ? '<div class="pd-chips">' + chips + '</div>' : '') +
      todayScheduleCard() +
      weatherCard() +
      choresCard() +
      tomorrowCard() +
      dinnerCard() +
      haCard() +
      '</div>';
  }

  function todayScheduleCard() {
    return '<section class="card a-sched">' +
      '<div class="card-head"><h2 class="lbl">Events</h2><span class="spacer"></span>' +
      countdownChips() + '</div>' +
      holidayStrip(state.today) +
      '<div class="scroll">' + scheduleList(state.today, true) + '</div>' +
      '</section>';
  }

  function tomorrowCard() {
    var d = addDays(state.today, 1);
    var evs = D.byDate[d] || [];
    var body;
    if (!evs.length) {
      body = '<div class="muted2" style="font-size:.9375rem">Nothing on yet.</div>';
    } else {
      body = '<div class="tmw-list">' + evs.slice(0, 8).map(function (e) {
        return '<button class="tmw-item" data-act="event" data-id="' + esc(e.id) +
          '" style="' + pstyle(e.person_id) + '">' +
          '<span class="dot"></span>' +
          '<span class="t mono">' + esc(e.all_day ? 'All day' : fmtTime(e.start)) + '</span>' +
          '<span class="n">' + esc(e.title) + '</span></button>';
      }).join('') + '</div>';
    }
    return '<section class="card a-tomorrow">' +
      '<div class="tmw"><h2 class="lbl">Tomorrow<br><span class="mono" style="letter-spacing:.02em;text-transform:none">' +
      esc(fmtDMY(d)) + '</span></h2>' + body + '</div></section>';
  }

  /* The day's sun path: a shallow arc from sunrise to sunset with the sun
     sitting where it actually is. The arc is a quadratic bezier whose x is
     exactly linear in t, so the time fraction maps straight onto it. */
  function sunArc(sun) {
    var rise = mins(sun.sunrise), set = mins(sun.sunset), now = nowMins();
    var span = Math.max(1, set - rise);
    var raw = (now - rise) / span;
    var down = raw < 0 || raw > 1;
    var t = Math.max(0, Math.min(1, raw));
    var y = 34 - 80 * t + 80 * t * t;                 // bezier y at t, viewBox units
    return '<div class="wx-arc">' +
      '<svg viewBox="0 0 100 36" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="M4 34 Q 50 -6 96 34" fill="none" stroke-width="1.4" ' +
      'stroke-linecap="round" stroke-dasharray="2 3" vector-effect="non-scaling-stroke"/></svg>' +
      '<span class="sun' + (down ? ' is-down' : '') + '" style="left:' +
      (4 + 92 * t).toFixed(1) + '%;top:calc(var(--arch) * ' + (y / 36).toFixed(4) + ')"></span>' +
      /* The <i> labels only surface when the arc folds flat on a short screen. */
      '<span class="end a mono"><i>Sunrise</i>' + esc(fmtTimeShort(sun.sunrise)) + '</span>' +
      '<span class="end b mono"><i>Sunset</i>' + esc(fmtTimeShort(sun.sunset)) + '</span>' +
      '</div>';
  }

  function weatherCard() {
    var w = D.weather;
    if (!w || !w.available || !w.daily || !w.daily.length) {
      return '<section class="card a-weather"><div class="card-head"><h2 class="lbl">Weather</h2></div>' +
        '<div class="empty">Weather unavailable — it will retry.</div></section>';
    }
    var today = w.daily[0];
    var cur = w.current || {};
    var code = cur.code != null ? cur.code : today.code;
    var group = wmoGroup(code);
    var temp = cur.temp != null ? Math.round(cur.temp) : Math.round(today.max);
    var sun = sunTimes();
    var week = w.daily.slice(0, 7).map(function (d, i) {
      var g = wmoGroup(d.code);
      var sunny = g === 'clear' || g === 'mostly' || g === 'partly';
      return '<div class="wx-day' + (i === 0 ? ' is-today' : '') + (sunny ? ' is-sun' : '') + '">' +
        '<div class="wd">' + esc(DOW_SHORT[dowOf(d.date)].slice(0, 3)) + '</div>' +
        wxGlyph(d.code) +
        '<div class="hi mono">' + Math.round(d.max) + '°</div>' +
        '<div class="lo mono">' + Math.round(d.min) + '°</div></div>';
    }).join('');
    var rain = Math.round(today.rain_prob || 0);
    var uv = today.uv != null ? Math.round(today.uv) : null;
    return '<section class="card wx a-weather">' +
      '<div class="wx-sky is-' + group + '">' +
      '<div class="card-head" style="margin-bottom:.5rem"><h2 class="lbl">Weather</h2>' +
      '<span class="spacer"></span><span class="muted2" style="font-size:.75rem">' +
      (state.theme === 'night' ? 'Sunrise ' + esc(fmtTimeShort(sun.sunrise)) : 'Sunset ' + esc(fmtTimeShort(sun.sunset))) +
      '</span></div>' +
      '<div class="wx-now"><span class="wx-glyph">' + wxGlyph(code) + '</span>' +
      '<span class="wx-temp mono">' + temp + '°</span>' +
      '<span class="wx-meta"><span class="d">' + esc(wxText(code, cur.description)) + '</span><br>' +
      '<span class="mono">' + Math.round(today.min) + '° / ' + Math.round(today.max) + '°</span></span></div>' +
      sunArc(sun) +
      '</div>' +
      '<div class="wx-body">' +
      '<div class="wx-stats">' +
      '<div class="wx-stat' + (rain >= 50 ? ' is-wet' : '') + '"><div class="k">Rain</div><div class="v mono">' + rain + '%</div></div>' +
      '<div class="wx-stat' + (uv != null && uv >= 8 ? ' is-hot' : '') + '"><div class="k">UV</div><div class="v mono">' + (uv != null ? uv : '–') + '</div></div>' +
      '<div class="wx-stat"><div class="k">Min</div><div class="v mono">' + Math.round(today.min) + '°</div></div>' +
      '<div class="wx-stat"><div class="k">Max</div><div class="v mono">' + Math.round(today.max) + '°</div></div>' +
      '</div><div class="wx-week">' + week + '</div></div></section>';
  }

  /* Stars spelled out in gold — the kid reads this from across the kitchen.
     One star per star earned, capped so a big week stays on one line; a
     single hollow star stands in for a week that hasn't started yet. */
  function starPips(n, cap, big) {
    n = Math.max(0, n | 0);
    var shown = Math.min(cap, n);
    var pips = '';
    for (var i = 0; i < Math.max(1, shown); i++) {
      pips += ICON.star.replace('<svg ', '<svg class="' + (i < shown ? 'on' : 'off') + '" ');
    }
    return '<span class="star-row' + (big ? ' big' : '') + '"><span class="pips">' + pips +
      '</span><span class="n">' + n + '</span></span>';
  }

  function choreRow(c, personId, compact) {
    return '<button class="chore' + (compact ? ' compact' : '') + (c.done ? ' is-done' : '') +
      '" data-act="chore" data-id="' + esc(c.id) + '" data-person="' + esc(personId) + '"' +
      ' style="' + pstyle(personId) + '">' +
      '<span class="tick">' + ICON.tick + '</span>' +
      '<span><span class="chore-name">' + esc(c.title) + '</span>' +
      (compact ? '' : '<br><span class="chore-slot">' + esc(c.slot) + '</span>') + '</span>' +
      '<span class="chore-stars mono">' + ICON.star + (c.stars || 1) + '</span></button>';
  }

  function choresCard() {
    var ch = D.chores;
    var body;
    if (!ch || !ch.people) {
      body = '<div class="empty">' + waitingCopy('Chores') + '</div>';
    } else {
      var withChores = ch.people.filter(function (p) { return p.chores && p.chores.length; });
      if (!withChores.length) {
        body = '<div class="empty">No chores today — nice one.</div>';
      } else {
        body = withChores.map(function (p) {
          return '<div class="chore-person" style="' + pstyle(p.id) + '"><div class="chore-head">' +
            '<span class="who">' + personChip(p.id, { bare: true }) + '</span>' +
            starPips(p.stars_week || 0, 6) + '</div>' +
            p.chores.map(function (c) { return choreRow(c, p.id, true); }).join('') +
            '</div>';
        }).join('');
      }
    }
    return '<section class="card a-chores"><div class="card-head"><h2 class="lbl">Chores</h2>' +
      '<span class="spacer"></span><span class="muted2" style="font-size:.75rem">This week</span></div>' +
      '<div class="scroll">' + body + '</div></section>';
  }

  /* Copy for a panel that has finished booting but still has no data for a
     card — say what is happening, don't apologise, don't sit on "Loading…". */
  function waitingCopy(what) {
    return state.booted
      ? what + " haven't synced yet — they'll appear here as soon as they do."
      : 'Loading…';
  }

  function mealFor(date, slot) {
    var mp = D.mealplan;
    if (!mp || !mp.days) return null;
    for (var i = 0; i < mp.days.length; i++) {
      if (mp.days[i].date === date) return mp.days[i][slot] || null;
    }
    return null;
  }

  function mealLabel(m) {
    if (!m) return null;
    return m.recipe_name || m.free_text || null;
  }

  /* -------------------------------------------------------- recipe bits -- */

  function recipeById(id) {
    var list = (D.recipes && D.recipes.recipes) || [];
    for (var i = 0; i < list.length; i++) if (String(list[i].id) === String(id)) return list[i];
    return null;
  }

  /* "https://www.recipetineats.com/x/" -> "recipetineats.com" */
  function recipeHost(r) {
    var u = r && r.source_url;
    if (!u) return null;
    var m = String(u).match(/^https?:\/\/([^/?#]+)/i);
    return m ? m[1].replace(/^www\./i, '') : null;
  }

  function recipeMinutes(r) {
    var m = (r && r.meta) || {};
    if (m.total_minutes) return m.total_minutes;
    var sum = (m.prep_minutes || 0) + (m.cook_minutes || 0);
    return sum || null;
  }

  /* Meta chips: time, servings, source. Falls back to the ingredient count
     so hand-entered recipes still say something useful. */
  function recipeChips(r, opts) {
    opts = opts || {};
    var m = (r && r.meta) || {}, out = [];
    var t = recipeMinutes(r);
    if (t) out.push('<span class="mchip warm">' + t + ' min</span>');
    if (m.servings) out.push('<span class="mchip">Serves ' + esc(m.servings) + '</span>');
    if (!opts.noSource) {
      var host = recipeHost(r);
      if (host) out.push('<span class="mchip src">' + esc(host) + '</span>');
    }
    if (!out.length && !opts.quiet) {
      out.push('<span class="mchip">' +
        plural((r.ingredients || []).length, 'ingredient', 'ingredients') + '</span>');
    }
    return out.join('');
  }

  /* ------------------------------------------------------- recipe photos -- */
  /* API.md: `meta.image_cached` means the backend holds a copy of the photo
     at /api/recipes/<id>/image. Everything else — the fourteen starters and
     anything typed in by hand — gets an illustrated tile instead of a hole in
     the grid. The demo fixtures carry `meta.image_data` (a data URI) so the
     mock never reaches for the network. */

  function recipePhotoSrc(r) {
    var m = (r && r.meta) || {};
    if (m.image_data) return m.image_data;
    if (m.image_cached && r.id != null) return '/api/recipes/' + encodeURIComponent(r.id) + '/image';
    return null;
  }

  /* FNV-1a with an avalanche finish — stable across reloads, so a recipe
     keeps its illustration for good. The finish matters: raw FNV's low bits
     barely move between strings as alike as recipe names, and `% 4` on them
     put eleven of the fourteen starters on the same accent. */
  function strHash(s) {
    var h = 2166136261;
    s = String(s == null ? '' : s);
    for (var i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    h ^= h >>> 15;
    h = Math.imul(h, 2246822507) >>> 0;
    h ^= h >>> 13;
    h = Math.imul(h, 3266489909) >>> 0;
    h ^= h >>> 16;
    return h >>> 0;
  }

  /* The illustrated stand-in: theme tokens for the ground, one family accent
     picked by name-hash for the dish. Inline (not a data URI) so the CSS
     custom properties resolve — the same markup redraws itself at night. */
  function recipePlaceholder(r) {
    var name = (r && r.name) || 'recipe';
    var h = strHash(name);
    /* colour and dish come off two independent hashes — one string's bits
       shifted are not random enough, and "three blue plates in a row" is
       exactly what makes a grid look broken rather than designed. */
    var c = 'var(--fam' + ((h % 4) + 1) + ')';
    /* the dish cycles with the id where there is one, so a fresh grid never
       lands three identical plates side by side; name-hashed otherwise */
    var dish = (r && typeof r.id === 'number' && isFinite(r.id))
      ? (Math.abs(r.id) % 4) : (strHash('dish:' + name) % 4);
    var art;
    if (dish === 0) {                                   // plate + cutlery
      art =
        '<circle cx="60" cy="46" r="26" fill="' + c + '" fill-opacity=".16" stroke="' + c + '" stroke-width="2.4"/>' +
        '<circle cx="60" cy="46" r="16" fill="none" stroke="' + c + '" stroke-width="1.4" stroke-opacity=".55"/>' +
        '<path d="M22 30v12a3 3 0 003 3h0v21M25 30v9M28 30v9M95 30c-3 0-5 4-5 8s2 6 5 6v22" ' +
        'fill="none" stroke="' + c + '" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" stroke-opacity=".7"/>';
    } else if (dish === 1) {                            // bowl + steam
      art =
        '<path d="M52 24c0-4 5-4 5-8M60.5 22c0-4 5-4 5-8M69 24c0-4 5-4 5-8" fill="none" stroke="' + c +
        '" stroke-width="2.2" stroke-linecap="round" stroke-opacity=".55"/>' +
        '<path d="M32 44h56a28 28 0 01-56 0z" fill="' + c + '" fill-opacity=".18" stroke="' + c +
        '" stroke-width="2.4" stroke-linejoin="round"/>' +
        '<path d="M27 44h66" stroke="' + c + '" stroke-width="2.6" stroke-linecap="round"/>' +
        '<path d="M45 52h30" stroke="' + c + '" stroke-width="1.6" stroke-linecap="round" stroke-opacity=".5"/>';
    } else if (dish === 2) {                            // pan + two eggs
      art =
        '<rect x="76" y="42" width="26" height="6.5" rx="3.25" fill="' + c + '" fill-opacity=".85"/>' +
        '<circle cx="52" cy="46" r="24" fill="' + c + '" fill-opacity=".16" stroke="' + c + '" stroke-width="2.4"/>' +
        '<circle cx="45" cy="41" r="7" fill="var(--card)" stroke="' + c + '" stroke-width="1.4" stroke-opacity=".5"/>' +
        '<circle cx="45" cy="41" r="2.6" fill="' + c + '" fill-opacity=".8"/>' +
        '<circle cx="58" cy="52" r="7" fill="var(--card)" stroke="' + c + '" stroke-width="1.4" stroke-opacity=".5"/>' +
        '<circle cx="58" cy="52" r="2.6" fill="' + c + '" fill-opacity=".8"/>';
    } else {                                            // pot + lid
      art =
        '<rect x="56" y="19" width="10" height="4.5" rx="2.25" fill="' + c + '" fill-opacity=".85"/>' +
        '<path d="M30 27h62" stroke="' + c + '" stroke-width="3" stroke-linecap="round"/>' +
        '<rect x="37" y="30" width="48" height="34" rx="7" fill="' + c + '" fill-opacity=".16" stroke="' + c +
        '" stroke-width="2.4"/>' +
        '<path d="M31 38h6M85 38h6" stroke="' + c + '" stroke-width="2.6" stroke-linecap="round"/>' +
        '<path d="M46 47h30M46 55h20" stroke="' + c + '" stroke-width="1.6" stroke-linecap="round" stroke-opacity=".5"/>';
    }
    return '<svg class="rp-ph" viewBox="0 0 120 90" preserveAspectRatio="xMidYMid slice" aria-hidden="true">' +
      '<rect width="120" height="90" fill="var(--card-2)"/>' +
      '<circle cx="60" cy="45" r="41" fill="' + c + '" fill-opacity=".06"/>' +
      '<ellipse cx="60" cy="76" rx="34" ry="4.5" fill="' + c + '" fill-opacity=".12"/>' +
      art + '</svg>';
  }

  /* Photo over illustration: when the cached photo 404s the <img> removes
     itself and the drawn tile underneath is what you see — never a broken
     image icon on a wall panel. */
  function recipeMedia(r, cls) {
    var src = recipePhotoSrc(r);
    return '<span class="rp-media' + (cls ? ' ' + cls : '') + '">' + recipePlaceholder(r) +
      (src ? '<img src="' + esc(src) + '" alt="" onerror="this.remove()">' : '') + '</span>';
  }

  function dinnerCard() {
    var m = mealFor(state.today, 'dinner');
    var label = mealLabel(m);
    var sub = '', chips = '';
    if (m && m.recipe_id) {
      var r = recipeById(m.recipe_id);
      if (r) {
        sub = r.notes || '';
        chips = recipeChips(r);
      }
    } else if (m && m.free_text) {
      sub = 'Planned by hand';
    }
    return '<section class="card dinner-card a-dinner" data-act="meal" data-date="' + esc(state.today) +
      '" data-slot="dinner" role="button" tabindex="0">' +
      '<div class="card-head"><h2 class="lbl">Dinner tonight</h2></div>' +
      (label
        ? '<div class="dinner-name">' + esc(label) + '</div>' +
          (sub ? '<div class="dinner-sub">' + esc(sub) + '</div>' : '') +
          (chips ? '<div class="dinner-meta">' + chips + '</div>' : '')
        : '<div class="empty" style="padding:0">Nothing planned — tap to add one.</div>') +
      '</section>';
  }

  /* --------------------------------------------------------------- HA row - */

  function tileBody(t) {
    var a = t.attrs || {};
    switch (t.type) {
      case 'sensor':
        return '<div class="t-value mono">' + esc(a.value != null ? a.value : '–') +
          (a.unit ? '<span style="font-size:.6em"> ' + esc(a.unit) + '</span>' : '') + '</div>';
      case 'climate':
        /* One room configured: the tile is that room, and still opens the
           whole-house sheet. Two or more and haCard() never draws this —
           it draws the single grouped Aircon tile instead. */
        return '<div class="t-value mono">' + esc(a.current_temp != null ? a.current_temp + '°' : '–') + '</div>' +
          '<div class="t-sub">Set ' + esc(a.setpoint != null ? a.setpoint + '°' : '–') +
          ' · ' + esc(hvacLabel(a.hvac_mode || t.state || 'off')) + '</div>';
      case 'cover':
        return '<div class="t-value mono" style="font-size:1.125rem">' + esc(capitalise(t.state || 'unknown')) + '</div>';
      case 'switch':
        return '<div class="t-value mono" style="font-size:1.125rem">' + esc(capitalise(t.state || 'unknown')) + '</div>';
      case 'media': {
        var vol = typeof a.volume === 'number' ? Math.max(0, Math.min(1, a.volume)) : null;
        return '<div class="t-value mono" style="font-size:1rem">' + esc(a.playing ? 'Playing' : 'Paused') + '</div>' +
          '<div class="t-sub">' + esc(a.title || '—') + (a.artist ? ' · ' + esc(a.artist) : '') + '</div>' +
          (vol == null ? '' :
            '<div class="vol-wrap"><span class="vol-bar"><i style="width:' +
            Math.round(vol * 100) + '%"></i></span>' +
            '<span class="vol-pct">' + Math.round(vol * 100) + '%</span></div>');
      }
      case 'fan': {
        var on = a.on != null ? !!a.on : t.state === 'on';
        var fp = typeof a.percentage === 'number' ? Math.round(a.percentage) : null;
        var sub = [];
        if (a.oscillating) sub.push('<span class="t-badge">Oscillating</span>');
        if (a.preset_mode) sub.push(esc(capitalise(a.preset_mode)));
        return '<div class="t-value mono" style="font-size:1.125rem">' + (on ? 'On' : 'Off') +
          (on && fp != null ? '<span class="t-pct mono"> · ' + fp + '%</span>' : '') + '</div>' +
          '<div class="t-sub">' + (sub.length ? sub.join(' ') : '—') + '</div>';
      }
      case 'solar':
        return solarTileBody(t);
      case 'presence':
        return '<div class="t-value" style="display:flex;align-items:center;gap:.5rem;font-size:1.125rem">' +
          '<span class="presence-dot" style="background:' + (a.home ? 'var(--ok)' : 'var(--hair)') + '"></span>' +
          (a.home ? 'Home' : 'Out') + '</div>';
      default:
        return '<div class="t-value mono" style="font-size:1.125rem">' + esc(capitalise(t.state || '–')) + '</div>';
    }
  }

  function capitalise(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }

  /* Confirm-tap protects the things you would hate to trigger by accident —
     covers, heating, switches. Media is harmless and fiddly, so play/pause
     and the volume steps fire on the first tap. */
  function tileActions(t) {
    if (!t.allow_action) return '';
    var b = [];
    var mk = function (action, label, value, now, cls) {
      return '<button class="t-btn' + (cls ? ' ' + cls : '') + '" data-act="ha" data-entity="' +
        esc(t.entity) + '" data-action="' + esc(action) + '"' +
        (value != null ? ' data-value="' + esc(value) + '"' : '') +
        (now ? ' data-now="1"' : '') +
        ' data-label="' + esc(label) + '">' + esc(label) + '</button>';
    };
    if (t.type === 'cover') { b.push(mk('open', 'Open')); b.push(mk('close', 'Close')); }
    else if (t.type === 'switch') {
      b.push(t.state === 'on' ? mk('turn_off', 'Turn off') : mk('turn_on', 'Turn on'));
    } else if (t.type === 'climate') {
      /* API.md: set_temperature acts immediately — the owner asked for the
         double tap to go. Only climate on/off still arms first, and that
         lives in the whole-house sheet. With more than one Sensibo the tile
         drops its keys entirely and becomes a read-out: three sets of
         steppers in one row is a thicket, and the sheet has the room. */
      if (climateTiles().length > 1) return '';
      var sp = (t.attrs && t.attrs.setpoint) != null ? t.attrs.setpoint : 23;
      b.push(mk('set_temperature', '−', sp - 1, true, 'vol'));
      b.push(mk('set_temperature', '+', sp + 1, true, 'vol'));
    } else if (t.type === 'media') {
      b.push((t.attrs && t.attrs.playing) ? mk('pause', 'Pause', null, true) : mk('play', 'Play', null, true));
      var vol = t.attrs && typeof t.attrs.volume === 'number' ? t.attrs.volume : null;
      if (vol != null) {
        b.push(mk('volume_set', '−', volStep(vol, -1), true, 'vol'));
        b.push(mk('volume_set', '+', volStep(vol, +1), true, 'vol'));
      }
    } else if (t.type === 'fan') {
      /* API.md: the fan's on/off is a confirm-tap like every other appliance;
         the percentage steps act at once, and so does oscillation — nudging
         the airflow is not the kind of thing you regret. */
      var fa = t.attrs || {};
      var fon = fa.on != null ? !!fa.on : t.state === 'on';
      b.push(fon ? mk('turn_off', 'Turn off') : mk('turn_on', 'Turn on'));
      b.push(fa.oscillating
        ? mk('oscillate_off', 'Osc off', null, true, 'osc')
        : mk('oscillate_on', 'Osc on', null, true, 'osc'));
      var fp = typeof fa.percentage === 'number' ? fa.percentage : 0;
      b.push(mk('set_percentage', '−', pctStep(fp, -1), true, 'vol'));
      b.push(mk('set_percentage', '+', pctStep(fp, +1), true, 'vol'));
    }
    return b.length ? '<div class="tile-acts">' + b.join('') + '</div>' : '';
  }

  /* 10-point steps for fan speed, clamped to the 0-100 the contract allows. */
  function pctStep(v, dir) {
    return Math.max(0, Math.min(100, Math.round((Number(v) || 0) / 10) * 10 + dir * 10));
  }

  /* 0.05 steps, clamped, rounded to kill float dust (0.35000000000000003). */
  function volStep(v, dir) {
    var next = Math.max(0, Math.min(1, (Number(v) || 0) + dir * 0.05));
    return Math.round(next * 100) / 100;
  }

  /* One counter, shared by everything that asks for a camera frame. The tile
     painter used to stamp Date.now() and the 10 s refresher used its own, so
     the two asked for different URLs for the same frame and the tablet cached
     both — around 8,600 disk writes a day per channel on eMMC. */
  var camStamp = 1;

  function camBust(url) {
    url = String(url || '');
    if (url.indexOf('data:') === 0) return url;      // the demo's inline frames
    /* Take our own stamp back off and leave any other query alone — a
       snapshot_url that carries a token has to survive the round trip. */
    var clean = url.replace(/([?&])t=\d+&?/, '$1').replace(/[?&]$/, '');
    return clean + (clean.indexOf('?') >= 0 ? '&' : '?') + 't=' + camStamp;
  }

  function camSrc(t) {
    var url = (t.attrs && t.attrs.snapshot_url) || ('/api/ha/camera/' + encodeURIComponent(t.entity));
    return camBust(url);
  }

  /* ------------------------------------------------- the grouped tiles ---- */
  /* The owner's ask, twice over: "group the AC's… same thing will happen with
     the Swann cameras, just need one button to open a view of all of them."
     So the HA row never grows a tile per room or per channel again — the
     climate entities collapse into one Aircon tile and the cameras into one
     Cameras tile, and each opens the panel that holds the whole set. */

  function cameraTiles() {
    return ((D.ha && D.ha.tiles) || []).filter(function (t) { return t.type === 'camera_snapshot'; });
  }

  /* A channel the DVR has stopped answering for. Nothing about the tile is
     wrong — the feed is — so it greys out and says so rather than vanishing. */
  function camOffline(t) {
    var s = String(t.state || '').toLowerCase();
    return s === 'unavailable' || s === 'offline' || s === 'unknown';
  }

  /* AIRCON — rooms-on count, the warmest room's temperature as the figure,
     and a three-row room·temp read-out underneath. Everything you would walk
     over to check, without walking over. */
  function airconTileBody(list) {
    var on = list.filter(function (t) {
      var s = String(t.state || (t.attrs && t.attrs.hvac_mode) || '').toLowerCase();
      return s && s !== 'off' && s !== 'unavailable';
    });
    var temps = list.map(function (t) { return t.attrs && t.attrs.current_temp; })
      .filter(function (v) { return typeof v === 'number' && isFinite(v); });
    var hot = temps.length ? Math.max.apply(null, temps) : null;

    var rows = list.slice(0, 3).map(function (t) {
      var s = String(t.state || (t.attrs && t.attrs.hvac_mode) || '').toLowerCase();
      var live = s && s !== 'off' && s !== 'unavailable';
      var ct = t.attrs && t.attrs.current_temp;
      return '<div class="ac-row' + (live ? ' is-on' : '') + '">' +
        '<span class="ac-dot"></span>' +
        '<span class="ac-n">' + esc(t.label) + '</span>' +
        '<span class="ac-t mono">' +
        esc(typeof ct === 'number' ? Math.round(ct * 10) / 10 + '°' : '–') + '</span></div>';
    }).join('');

    return '<div class="ac-head">' +
      '<span class="t-value mono">' + esc(hot != null ? Math.round(hot * 10) / 10 + '°' : '–') + '</span>' +
      '<span class="ac-on' + (on.length ? ' is-live' : '') + '">' +
      esc(on.length ? on.length + ' on' : 'All off') + '</span></div>' +
      '<div class="ac-rows">' + rows +
      (list.length > 3 ? '<div class="ac-more">+' + (list.length - 3) + ' more</div>' : '') +
      '</div>';
  }

  function airconTile(list, off) {
    /* One room and it is simply that room's tile — same tap, same sheet. */
    if (list.length === 1) {
      var t = list[0];
      return '<div class="tile is-climate' + (off ? ' is-off' : '') + '"' +
        (off ? '' : ' data-act="climate-open" role="button" tabindex="0"') + '>' +
        '<div class="t-label">' + esc(t.label) + '</div>' + tileBody(t) +
        (off ? '' : tileActions(t)) + '</div>';
    }
    return '<div class="tile is-aircon' + (off ? ' is-off' : '') + '"' +
      (off ? '' : ' data-act="climate-open" role="button" tabindex="0"') + '>' +
      '<div class="t-label">Aircon<span class="t-count">' + list.length + '</span></div>' +
      airconTileBody(list) + '</div>';
  }

  /* CAMERAS — one tile carrying a live thumbnail that steps through the
     channels every 10 s (the same beat the snapshots refresh on), with the
     channel count as a badge. Tapping opens the wall of all of them. */
  function camCycleTile(list, off) {
    var i = list.length ? (state.camIdx % list.length) : 0;
    var t = list[i];
    var dead = off || camOffline(t);
    return '<div class="tile is-cams' + (off ? ' is-off' : '') + '">' +
      '<button class="cam-wrap" data-act="cams" data-entity="' + esc(t.entity) + '"' +
      ' aria-label="Cameras — ' + esc(list.length) + ' channels">' +
      (dead
        ? '<span class="cam-dead"><span>No signal</span></span>'
        : '<img data-cam="' + esc(t.entity) + '" decoding="async" src="' + esc(camSrc(t)) +
          '" alt="' + esc(t.label) + ' camera">') +
      '<span class="cam-label">' + esc(t.label) + '</span>' +
      (list.length > 1 ? '<span class="cam-count mono">' + list.length + '</span>' : '') +
      '</button></div>';
  }

  /* The cycle rides the camera refresh timer, so the thumbnail and the image
     behind it move together instead of fighting. */
  function stepCameraCycle() {
    var list = cameraTiles();
    if (list.length < 2) return;
    state.camIdx = (state.camIdx + 1) % list.length;
    var host = document.querySelector('.tile.is-cams');
    if (!host) return;
    var t = list[state.camIdx];
    var img = host.querySelector('img[data-cam]');
    /* A live thumbnail swapping to a live thumbnail is a one-line change; any
       crossing of the offline boundary redraws the tile from scratch. */
    if (img && !camOffline(t)) {
      host.querySelector('.cam-wrap').dataset.entity = t.entity;
      host.querySelector('.cam-label').textContent = t.label;
      img.dataset.cam = t.entity;
      img.src = camSrc(t);
      img.alt = t.label + ' camera';
    } else {
      host.outerHTML = camCycleTile(list, !!(D.ha && D.ha.available === false));
    }
  }

  /* THE CAMERA WALL — every configured channel at once, which is the thing
     the owner actually asked for. Labels on, 10 s cache-busted refresh (the
     shared refreshCameras() timer finds these images too), and any one of
     them opens full size with a way back to the wall. */
  function cameraGridHtml() {
    var list = cameraTiles();
    if (!list.length) return '<div class="empty">No cameras are configured on the panel yet.</div>';
    var off = !!(D.ha && D.ha.available === false);
    var live = list.filter(function (t) { return !off && !camOffline(t); }).length;
    return '<div class="cam-wall">' +
      '<button class="close-x cam-wall-close" data-act="close-sheet" aria-label="Close">' + ICON.close + '</button>' +
      '<div class="cam-wall-head"><span class="np-label">Cameras</span>' +
      '<span class="cam-wall-n mono">' + esc(live + ' of ' + list.length + ' live') + '</span></div>' +
      '<div class="cam-grid">' + list.map(function (t) {
        var dead = off || camOffline(t);
        return '<button class="cam-cell' + (dead ? ' is-dead' : '') + '"' +
          (dead ? ' disabled' : ' data-act="cam" data-entity="' + esc(t.entity) + '" data-from="grid"') + '>' +
          (dead
            ? '<span class="cam-dead"><span>No signal</span></span>'
            : '<img data-cam="' + esc(t.entity) + '" decoding="async" src="' + esc(camSrc(t)) +
              '" alt="' + esc(t.label) + ' camera">') +
          '<span class="cam-label">' + esc(t.label) + '</span></button>';
      }).join('') + '</div>' +
      (off ? '<p class="cam-wall-note">Home Assistant is offline — these are the last frames it sent.</p>' : '') +
      '</div>';
  }

  function openCameraGrid() {
    var list = cameraTiles();
    /* One channel in the house and the wall would be a wall of one — go
       straight to the big picture, which is what the tap meant anyway. */
    if (list.length === 1 && !camOffline(list[0])) { openCamera(list[0].entity); return; }
    openSheet(cameraGridHtml(), { raw: true, centre: true });
  }

  function haCard() {
    var ha = D.ha;
    var tiles = (ha && ha.tiles) || [];
    var off = ha && ha.available === false;
    if (!tiles.length) {
      return '<section class="card a-ha">' +
        '<div class="ha-row"><span class="lbl">Home</span>' +
        '<span class="muted2" style="font-size:.875rem;align-self:center;padding-left:1rem">' +
        (ha ? 'No tiles configured yet — add them to config.yaml.' : waitingCopy('Home tiles')) +
        '</span></div></section>';
    }
    /* Climate entities and camera channels each collapse to a single tile,
       drawn where the first one of its kind sat so the row keeps the order
       config.yaml asked for. Everything else is one tile per entity. */
    var clims = climateTiles();
    var cams = cameraTiles();
    var drawnClim = false, drawnCam = false;

    var body = tiles.map(function (t) {
      if (t.type === 'climate') {
        if (drawnClim) return '';
        drawnClim = true;
        return airconTile(clims, off);
      }
      if (t.type === 'camera_snapshot') {
        if (drawnCam) return '';
        drawnCam = true;
        return camCycleTile(cams, off);
      }
      /* The media tile opens the now-playing overlay when tapped anywhere its
         own buttons aren't — they sit deeper, so delegation finds them first. */
      var media = t.type === 'media' && !off;
      /* the solar tile is read-only everywhere — its whole tap is the
         power-flow overlay, and it carries no buttons at all */
      var sol = t.type === 'solar' && !off;
      return '<div class="tile' + (off ? ' is-off' : '') + (media ? ' is-media' : '') +
        (t.type === 'fan' ? ' is-fan' : '') +
        (t.type === 'solar' ? ' is-solar' : '') + '"' +
        (media ? ' data-act="media-open" data-entity="' + esc(t.entity) + '" role="button" tabindex="0"' : '') +
        (sol ? ' data-act="solar-open" role="button" tabindex="0"' : '') + '>' +
        '<div class="t-label">' + esc(t.label) + '</div>' + tileBody(t) +
        (off ? '' : tileActions(t)) + '</div>';
    }).join('');
    return '<section class="card a-ha">' +
      '<div class="ha-row">' + body +
      (off ? '<span class="ha-offline">HA offline — showing last known</span>' : '') +
      '</div></section>';
  }

  /* ------------------------------------------------------- now playing ---- */
  /* The media tile blown up to arm's length: title, artist, one big transport
     key and a volume bar you can hit from across the kitchen. HA may start
     sending `entity_picture` later — until then the art well draws a vinyl
     disc in the family's own four colours rather than pulling an image. */

  function mediaTile(entity) {
    var tiles = (D.ha && D.ha.tiles) || [];
    for (var i = 0; i < tiles.length; i++) if (tiles[i].entity === entity) return tiles[i];
    return null;
  }

  function npArt(t) {
    var a = t.attrs || {};
    if (a.entity_picture) {
      /* The art is proxied through the panel (HA's own entity_picture path is
         relative to HA's origin and 404s here). If that proxy fails anyway —
         art pulled mid-track, a stream with none — drop back to the record
         sleeve rather than leaving a broken-image glyph on the wall. */
      return '<img class="np-img" src="' + esc(a.entity_picture) + '" alt="" ' +
        'data-entity="' + esc(t.entity) + '" onerror="window.__artFail(this)">';
    }
    /* Nothing queued: a plain speaker glyph rather than a record of silence. */
    if (!a.title) return '<span class="np-glyph">' + ICON.speaker + '</span>';
    var rings = ['--fam1', '--fam2', '--fam3', '--fam4'].map(function (v, i) {
      return '<circle cx="100" cy="100" r="' + (86 - i * 13) + '" fill="none" stroke="var(' + v + ')" ' +
        'stroke-width="' + (7 - i) + '" opacity="' + (0.78 - i * 0.08).toFixed(2) + '"/>';
    }).join('');
    return '<div class="np-disc' + (a.playing ? ' is-spin' : '') + '" aria-hidden="true">' +
      '<svg viewBox="0 0 200 200">' +
      '<circle cx="100" cy="100" r="98" class="np-vinyl"/>' + rings +
      '<circle cx="100" cy="100" r="30" fill="var(--fam3)" opacity="0.92"/>' +
      '<circle cx="100" cy="100" r="7" class="np-spindle"/></svg></div>';
  }

  /* Which favourite we just asked for, if the speaker hasn't caught up yet.
     Sonos takes a couple of seconds to swap a stream, and the 15 s poll is
     slower still, so the row says "Starting…" until `attrs.source` agrees. */
  function startingSource(t) {
    var s = state.npStart;
    if (!s || s.entity !== t.entity) return null;
    if (Date.now() - s.at > 20000) { state.npStart = null; return null; }
    if ((t.attrs || {}).source === s.name) { state.npStart = null; return null; }
    return s.name;
  }

  /* A saved radio station reads differently from a playlist, so the row
     glyph says which — guessed from the name, since HA doesn't tell us. */
  function sourceIcon(name) {
    return /\b(fm|am|radio|abc|triple|double j|classic|news|jazz|kids listen)\b/i.test(String(name || ''))
      ? ICON.radio : ICON.playlist;
  }

  function sourceRows(t) {
    var a = t.attrs || {};
    var list = (a.source_list || []).filter(function (x) { return x; });
    if (!list.length) {
      return '<p class="np-none muted2">Add favourites in the Sonos app and they’ll appear here.</p>';
    }
    var starting = startingSource(t);
    return '<div class="np-src-list">' + list.map(function (name) {
      var on = !starting && a.source === name;
      var busy = starting === name;
      return '<button class="np-src' + (on ? ' is-on' : '') + (busy ? ' is-starting' : '') +
        '" data-act="ha" data-now="1" data-entity="' + esc(t.entity) +
        '" data-action="select_source" data-value="' + esc(name) + '" data-vstr="1" data-label="' + esc(name) + '">' +
        '<span class="np-src-ic">' + sourceIcon(name) + '</span>' +
        '<span class="np-src-n">' + esc(name) + '</span>' +
        (busy ? '<span class="np-src-tag">Starting…</span>'
              : (on ? '<span class="np-src-tag is-on">Playing</span>' : '')) +
        '</button>';
    }).join('') + '</div>';
  }

  function nowPlayingHtml(t) {
    var a = t.attrs || {};
    var vol = typeof a.volume === 'number' ? Math.max(0, Math.min(1, a.volume)) : null;
    var pct = vol == null ? null : Math.round(vol * 100);
    var playing = !!a.playing;
    var can = !!t.allow_action;
    var starting = startingSource(t);
    var btn = function (action, label, value, cls) {
      return '<button class="' + cls + '" data-act="ha" data-now="1" data-entity="' + esc(t.entity) +
        '" data-action="' + action + '"' + (value != null ? ' data-value="' + value + '"' : '') +
        ' data-label="' + esc(label) + '" aria-label="' + esc(label) + '">';
    };
    var out =
      '<button class="close-x np-close" data-act="close-sheet" aria-label="Close">' + ICON.close + '</button>' +
      '<div class="np-art">' + npArt(t) + '</div>' +
      '<div class="np-info">' +
      '<div class="np-label">' + esc(t.label) + ' · ' +
      (starting ? 'Starting' : (playing ? 'Now playing' : 'Paused')) + '</div>' +
      '<div class="np-title">' + esc(starting ? starting : (a.title || 'Nothing playing')) + '</div>' +
      '<div class="np-artist">' + esc(starting ? 'Starting…' : (a.artist || (a.source || '—'))) + '</div>';

    if (!can) {
      out += '<p class="muted2 np-ro">This speaker is read-only on the panel.</p>';
    } else {
      out += '<div class="np-transport">' +
        btn('previous', 'Previous track', null, 'np-skip') + ICON.prev + '</button>' +
        btn(playing ? 'pause' : 'play', playing ? 'Pause' : 'Play', null, 'np-play') +
        (playing ? ICON.pause : ICON.play) + '<span>' + (playing ? 'Pause' : 'Play') + '</span></button>' +
        btn('next', 'Next track', null, 'np-skip') + ICON.next + '</button>' +
        '</div>';
      if (vol != null) {
        out += '<div class="np-vol">' +
          btn('volume_set', 'Quieter', volStep(vol, -1), 'np-vbtn') + '−</button>' +
          '<div class="np-bar" data-act="np-seek" data-entity="' + esc(t.entity) + '" role="slider" tabindex="0" ' +
          'aria-label="Volume" aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + pct + '">' +
          '<i style="width:' + pct + '%"></i></div>' +
          btn('volume_set', 'Louder', volStep(vol, +1), 'np-vbtn') + '+</button>' +
          '<span class="np-pct mono">' + pct + '%</span></div>';
      }
      /* The owner's ask: pick what to play without leaving the panel. The
         favourites are whatever is saved in the Sonos app. */
      out += '<div class="np-sources"><div class="np-sec">Play something</div>' + sourceRows(t) + '</div>';
    }
    return out + '</div>';
  }

  /* Called from the art <img>'s onerror — forget the picture for this track
     and redraw, which falls through to the record sleeve. The next poll will
     offer a fresh URL if the track changes. */
  window.__artFail = function (img) {
    var t = mediaTile(img.getAttribute('data-entity'));
    if (t && t.attrs) t.attrs.entity_picture = null;
    updateNowPlaying();
  };

  function openNowPlaying(entity) {
    var t = mediaTile(entity);
    if (!t) return;
    openSheet('<div class="np" id="npCard" role="dialog" aria-modal="true" aria-label="Now playing">' +
      nowPlayingHtml(t) + '</div>', { raw: true, centre: true });
    state.np = entity;
  }

  /* Keep the overlay in step with the 15 s HA poll and with optimistic taps. */
  function updateNowPlaying() {
    if (!state.np) return;
    var host = byId('npCard');
    if (!host) { state.np = null; return; }
    var t = mediaTile(state.np);
    if (!t) return;
    /* the 15 s poll redraws the whole card — hold the favourites list where
       the reader left it rather than snapping it back to the top */
    var list = host.querySelector('.np-src-list');
    var top = list ? list.scrollTop : 0;
    host.innerHTML = nowPlayingHtml(t);
    list = host.querySelector('.np-src-list');
    if (list && top) list.scrollTop = top;
  }

  function npSeek(el, clientX) {
    var r = el.getBoundingClientRect();
    if (!r.width) return;
    var v = Math.round(Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * 20) / 20;
    var t = mediaTile(el.dataset.entity);
    if (t && t.attrs) { t.attrs.volume = v; render(); }
    act(POST('/api/ha/action', { entity: el.dataset.entity, action: 'volume_set', value: v }), function () {
      setTimeout(function () { loadHa().then(renderSoon); }, 700);
    });
  }

  /* ----------------------------------------------------- whole-house aircon */
  /* The house has three Sensibos, so "the aircon panel" is not one tile — it
     is a room list. Tapping any climate tile opens this sheet: every
     configured climate entity, one generous row each, current temperature
     large enough to read from the bench, a setpoint stepper that acts on the
     first tap, and an on/off that still arms before it fires. */

  function climateTiles() {
    return ((D.ha && D.ha.tiles) || []).filter(function (t) { return t.type === 'climate'; });
  }

  /* HA's hvac modes read like enum values; the wall says what it is doing. */
  var HVAC_TEXT = {
    cool: 'Cooling', heat: 'Heating', dry: 'Drying', fan_only: 'Fan only',
    auto: 'Auto', heat_cool: 'Auto', off: 'Off'
  };

  function hvacLabel(mode) {
    var k = String(mode || 'off').toLowerCase();
    return HVAC_TEXT[k] || capitalise(k.replace(/_/g, ' '));
  }

  function climateRow(t) {
    var a = t.attrs || {};
    var raw = String(t.state || a.hvac_mode || '').toLowerCase();
    var on = raw && raw !== 'off' && raw !== 'unavailable';
    var sp = a.setpoint != null ? Number(a.setpoint) : 23;
    var mode = a.hvac_mode || t.state || 'off';
    var step = function (label, value) {
      return '<button class="t-btn cl-step" data-act="ha" data-now="1" data-entity="' + esc(t.entity) +
        '" data-action="set_temperature" data-value="' + esc(value) +
        '" data-label="' + esc(label) + '" aria-label="' + esc(label) + '">' + esc(label) + '</button>';
    };
    var pow = t.allow_action
      ? '<button class="t-btn cl-pow" data-act="ha" data-entity="' + esc(t.entity) +
        '" data-action="' + (on ? 'turn_off' : 'turn_on') + '" data-label="' +
        (on ? 'Turn off' : 'Turn on') + '">' + (on ? 'Turn off' : 'Turn on') + '</button>'
      : '<span class="cl-ro muted2">Read-only</span>';

    return '<div class="cl-row' + (on ? ' is-on' : '') + '">' +
      '<div class="cl-id"><div class="cl-name">' + esc(t.label) + '</div>' +
      '<div class="cl-mode"><span class="cl-dot"></span>' +
      esc(on ? hvacLabel(mode) : 'Off') + '</div></div>' +
      '<div class="cl-now"><div class="v mono">' +
      esc(a.current_temp != null ? Math.round(a.current_temp * 10) / 10 + '°' : '–') +
      '</div><div class="k">Now</div></div>' +
      (t.allow_action
        ? '<div class="cl-set">' + step('−', sp - 1) +
          '<div class="cl-sp"><div class="v mono">' + esc(sp) + '°</div><div class="k">Set to</div></div>' +
          step('+', sp + 1) + '</div>'
        : '<div class="cl-set"><div class="cl-sp"><div class="v mono">' + esc(sp) +
          '°</div><div class="k">Set to</div></div></div>') +
      '<div class="cl-act">' + pow + '</div></div>';
  }

  function climateSheetHtml() {
    var list = climateTiles();
    if (!list.length) {
      return '<div class="empty">No aircon is configured on the panel yet.</div>';
    }
    return (D.ha && D.ha.available === false
      ? '<div class="note">Home Assistant is offline — this is the last it told us.</div>' : '') +
      '<div class="cl-list">' + list.map(climateRow).join('') + '</div>' +
      '<p class="muted2 cl-foot">Temperature changes go straight through. ' +
      'Turning a room on or off asks twice.</p>';
  }

  function openClimateSheet() {
    openSheet('<div id="climateCard">' + climateSheetHtml() + '</div>', {
      title: 'Aircon', focus: false,
      foot: '<span style="flex:1 1 auto"></span>' +
        '<button class="btn ghost" data-act="close-sheet">Close</button>'
    });
    state.climate = true;
  }

  /* Keep the sheet in step with the 15 s HA poll and with optimistic taps —
     but never redraw out from under a button that is waiting for its second
     tap, or the confirm would vanish mid-gesture. */
  function updateClimateSheet() {
    if (!state.climate) return;
    var host = byId('climateCard');
    if (!host) { state.climate = false; return; }
    if (host.querySelector('[data-armed="1"]')) return;
    host.innerHTML = climateSheetHtml();
  }

  /* ------------------------------------------------------- solar + battery - */
  /* API.md: the solar tile carries `{pv_w, load_w, grid_w, battery_w,
     battery_pct}` — watts or null, **battery_w positive = charging, grid_w
     positive = exporting** — and is read-only, so nothing here ever posts an
     action. The compact tile is the glance; tapping it opens the power-flow
     overlay, which is where the house's energy story is actually told. */

  /* An inverter never sits at exactly zero: a few watts of self-consumption
     or measurement noise is "idle", not a flow. */
  var W_DEAD = 50;

  function solarTile() {
    var tiles = (D.ha && D.ha.tiles) || [];
    for (var i = 0; i < tiles.length; i++) if (tiles[i].type === 'solar') return tiles[i];
    return null;
  }

  function numOrNull(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }

  /* Watts in, one-decimal kilowatts out. Nulls read as an em-dash — the panel
     never invents a zero it wasn't told about. */
  function kwText(w) {
    if (w == null) return '—';
    return (Math.round(Math.abs(w) / 100) / 10).toFixed(1);
  }

  /* "5.2 kW" with a hard space: a wrapped figure reads as two numbers. */
  function kwUnit(w) { return kwText(w) + '\u00A0kW'; }

  /* Which way the power is moving, derived from the two signed numbers the
     contract gives us:
        battery_w > 0  charging   → solar feeds the battery
        battery_w < 0  discharging→ battery feeds the house
        grid_w    > 0  exporting  → solar feeds the grid
        grid_w    < 0  importing  → grid feeds the house
     whatever the roof makes that isn't charging or exporting is going to the
     house. When the panels are known to be dark the charge can only be coming
     off the mains, so the solar→battery arm stays quiet rather than claiming
     sunshine that isn't there. */
  function solarFlow(t) {
    var a = (t && t.attrs) || {};
    var pv = numOrNull(a.pv_w), load = numOrNull(a.load_w),
        grid = numOrNull(a.grid_w), bat = numOrNull(a.battery_w),
        pct = numOrNull(a.battery_pct);
    var f = {
      pv: pv, load: load, grid: grid, bat: bat, pct: pct,
      dead: pv == null && load == null && grid == null && bat == null && pct == null,
      producing: pv != null && pv > W_DEAD,
      charging: bat != null && bat > W_DEAD,
      discharging: bat != null && bat < -W_DEAD,
      exporting: grid != null && grid > W_DEAD,
      importing: grid != null && grid < -W_DEAD,
      sb: 0, sg: 0, sh: 0, bh: 0, gh: 0
    };
    if (f.charging) f.sb = pv == null ? bat : Math.min(bat, Math.max(pv, 0));
    if (f.discharging) f.bh = -bat;
    if (f.exporting) f.sg = grid;
    if (f.importing) f.gh = -grid;
    if (pv != null) {
      f.sh = Math.max(0, pv - f.sb - f.sg);
      if (load != null) f.sh = Math.min(f.sh, Math.max(load, 0));
    }
    f.amount = { sb: f.sb, sg: f.sg, sh: f.sh, bh: f.bh, gh: f.gh };
    return f;
  }

  function flowLive(f, k) { return f.amount[k] > W_DEAD; }

  /* ---- the compact tile ---- */

  function solarTileBody(t) {
    var f = solarFlow(t);
    var batCls = f.charging ? ' is-chg' : (f.discharging ? ' is-dis' : '');
    var arrow = f.charging ? ICON.up : (f.discharging ? ICON.down : '');
    var pill = '<span class="sol-batt' + batCls + '">' +
      (arrow ? '<span class="sol-arw">' + arrow + '</span>' : '') +
      '<b class="mono">' + (f.pct == null ? '—' : Math.round(f.pct) + '%') + '</b></span>';

    var grid;
    if (f.grid == null) {
      grid = '<span class="sol-grid"><span class="d">grid</span><b class="mono">—</b></span>';
    } else if (f.exporting || f.importing) {
      grid = '<span class="sol-grid' + (f.importing ? ' is-imp' : ' is-exp') + '">' +
        '<span class="d">' + (f.exporting ? '→ grid' : 'grid →') + '</span>' +
        '<b class="mono">' + kwText(f.grid) + '</b><span class="u">kW</span></span>';
    } else {
      grid = '<span class="sol-grid"><span class="d">grid idle</span></span>';
    }

    return '<div class="sol-main">' +
      '<span class="sol-sun' + (f.producing ? ' is-on' : '') + '">' + ICON.sun + '</span>' +
      '<span class="sol-pv mono">' + kwText(f.pv) + '</span>' +
      '<span class="sol-u">kW</span></div>' +
      '<div class="sol-chips">' + pill + grid + '</div>';
  }

  /* ---- the power-flow overlay ---- */

  /* Four nodes on a diamond — sun at the top, house at the foot, battery and
     grid on the shoulders — and the five arms energy can actually take. Each
     arm is drawn once, in the direction it flows, so a single `is-live` class
     is all the animation needs and nothing ever has to run backwards. */
  var PF_NODES = [
    { k: 'solar', c: [190, 66], g: GLYPH.sun, label: 'Solar' },
    { k: 'bat', c: [58, 214], g: null, label: 'Battery' },
    { k: 'grid', c: [322, 214], g: GLYPH.pylon, label: 'Grid' },
    { k: 'home', c: [190, 358], g: GLYPH.house, label: 'Home' }
  ];
  var PF_R = 42;                 // node radius, in diagram units

  /* Each arm: the quadratic it is drawn along (start, control, end — always
     from source to destination), where its kW label sits along that curve,
     and where the arrowhead lands. lt/at are positions along the curve, so
     the labels stay clear of the node captions no matter the arm. */
  var PF_FLOWS = [
    { k: 'sb', p: [[162, 97], [108, 112], [86, 183]], lt: 0.7, at: 0.92, cls: 'f-bat' },
    { k: 'sg', p: [[218, 97], [272, 112], [294, 183]], lt: 0.7, at: 0.92, cls: 'f-sun' },
    { k: 'sh', p: [[190, 108], [190, 212], [190, 316]], lt: 0.5, at: 0.9, cls: 'f-sun' },
    { k: 'bh', p: [[86, 245], [96, 300], [162, 327]], lt: 0.62, at: 0.92, cls: 'f-bat' },
    { k: 'gh', p: [[294, 245], [284, 300], [218, 327]], lt: 0.62, at: 0.92, cls: 'f-grid' }
  ];

  /* point on the quadratic, and the direction it is heading there */
  function pfAt(p, t) {
    var u = 1 - t;
    return [u * u * p[0][0] + 2 * u * t * p[1][0] + t * t * p[2][0],
            u * u * p[0][1] + 2 * u * t * p[1][1] + t * t * p[2][1]];
  }

  function pfAngle(p, t) {
    var u = 1 - t;
    var dx = 2 * u * (p[1][0] - p[0][0]) + 2 * t * (p[2][0] - p[1][0]);
    var dy = 2 * u * (p[1][1] - p[0][1]) + 2 * t * (p[2][1] - p[1][1]);
    return Math.atan2(dy, dx) * 180 / Math.PI;
  }

  function pfNode(n) {
    var x = n.c[0], y = n.c[1];
    var inner = n.k === 'bat'
      /* the battery draws itself: a cell with a fill level, not a glyph */
      ? '<clipPath id="pfClip"><rect x="' + (x - 17) + '" y="' + (y - 18) +
        '" width="34" height="36" rx="6"/></clipPath>' +
        '<rect class="pf-cell" x="' + (x - 17) + '" y="' + (y - 18) + '" width="34" height="36" rx="6"/>' +
        '<rect class="pf-cap" x="' + (x - 6) + '" y="' + (y - 25) + '" width="12" height="7" rx="2.5"/>' +
        '<rect class="pf-fill" id="pfFill" clip-path="url(#pfClip)" x="' + (x - 17) +
        '" y="' + (y + 18) + '" width="34" height="0"/>'
      : '<g class="pf-gl" transform="translate(' + (x - 22.8) + ',' + (y - 22.8) + ') scale(1.9)" ' +
        'stroke-width="1.15">' + n.g + '</g>';
    return '<g class="pf-node" id="pfN_' + n.k + '">' +
      '<circle class="pf-ring" cx="' + x + '" cy="' + y + '" r="' + PF_R + '"/>' + inner +
      '<text class="pf-nlab" id="pfNL_' + n.k + '" x="' + x + '" y="' + (y + PF_R + 20) +
      '" text-anchor="middle">' + esc(n.label) + '</text></g>';
  }

  function pfFlow(f) {
    var p = f.p;
    var d = 'M' + p[0][0] + ' ' + p[0][1] + ' Q' + p[1][0] + ' ' + p[1][1] +
      ' ' + p[2][0] + ' ' + p[2][1];
    var a = pfAt(p, f.at), l = pfAt(p, f.lt);
    return '<g class="pf-arm ' + f.cls + '" id="pfG_' + f.k + '">' +
      '<path class="pf-path" id="pfP_' + f.k + '" d="' + d + '"/>' +
      '<polygon class="pf-arrow" points="-6,-6.5 8,0 -6,6.5" ' +
      'transform="translate(' + a[0].toFixed(1) + ',' + a[1].toFixed(1) + ') rotate(' +
      pfAngle(p, f.at).toFixed(1) + ')"/>' +
      '<g class="pf-lab" id="pfL_' + f.k + '">' +
      '<rect x="' + (l[0] - 31).toFixed(1) + '" y="' + (l[1] - 12).toFixed(1) +
      '" width="62" height="24" rx="12"/>' +
      '<text x="' + l[0].toFixed(1) + '" y="' + (l[1] + 5).toFixed(1) +
      '" text-anchor="middle">—</text></g></g>';
  }

  function pfSvg() {
    return '<svg class="pf-svg" viewBox="0 0 380 434" role="img" ' +
      'aria-label="Power flow between the solar panels, the battery, the grid and the house" ' +
      'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">' +
      PF_FLOWS.map(pfFlow).join('') + PF_NODES.map(pfNode).join('') + '</svg>';
  }

  function pfFig(k, label) {
    return '<div class="pf-fig" id="pfF_' + k + '">' +
      '<div class="k">' + esc(label) + '</div>' +
      '<div class="v mono" id="pfV_' + k + '">—</div>' +
      '<div class="s" id="pfS_' + k + '">—</div></div>';
  }

  function solarSheetHtml(t) {
    return '<button class="close-x pf-close" data-act="close-sheet" aria-label="Close">' + ICON.close + '</button>' +
      '<div class="pf-diagram">' + pfSvg() + '</div>' +
      '<div class="pf-side">' +
      '<div class="pf-title">' + esc(t.label || 'Solar') + '</div>' +
      '<div class="pf-figs">' +
      pfFig('pv', 'Solar now') + pfFig('home', 'The house') +
      pfFig('bat', 'Battery') + pfFig('grid', 'Grid') +
      '</div>' +
      '<p class="pf-note" id="pfNote"></p></div>';
  }

  /* Where the house's power is coming from, in words. */
  function homeSources(f) {
    var from = [];
    if (flowLive(f, 'sh')) from.push('the sun');
    if (flowLive(f, 'bh')) from.push('the battery');
    if (flowLive(f, 'gh')) from.push('the mains');
    if (!from.length) return f.load == null ? 'no reading' : 'nothing drawing right now';
    if (from.length === 1) return 'from ' + from[0];
    return 'from ' + from.slice(0, -1).join(', ') + ' and ' + from[from.length - 1];
  }

  function setText(id, s) { var el = byId(id); if (el) el.textContent = s; }

  function setFig(k, value, sub, on) {
    setText('pfV_' + k, value);
    setText('pfS_' + k, sub);
    var box = byId('pfF_' + k);
    if (box) box.classList.toggle('is-on', !!on);
  }

  /* The 15 s poll lands here, not on innerHTML: the arms keep their CSS
     animation phase because their classes and geometry never change — only
     text, opacity and the battery's fill level are touched. */
  function paintSolar() {
    if (!state.solar) return;
    var host = byId('pfCard');
    if (!host) { state.solar = false; return; }
    var t = solarTile();
    if (!t) return;
    var f = solarFlow(t);
    var off = D.ha && D.ha.available === false;

    var svgEl = host.querySelector('.pf-svg');
    if (svgEl) svgEl.classList.toggle('is-ghost', f.dead);

    /* An export while the roof is known to be dark is the battery selling,
       not the sun: the arm is the only way out to the grid, so it stays —
       but it takes the battery's colour rather than claiming sunshine. */
    var altSg = f.exporting && f.pv != null && !f.producing;
    PF_FLOWS.forEach(function (arm) {
      var live = !f.dead && flowLive(f, arm.k);
      var g = byId('pfG_' + arm.k);
      if (g) {
        g.classList.toggle('is-live', live);
        if (arm.k === 'sg') g.classList.toggle('is-alt', !!altSg);
      }
      var lab = byId('pfL_' + arm.k);
      if (lab) {
        var txt = lab.querySelector('text');
        if (txt) txt.textContent = kwUnit(f.amount[arm.k]);
      }
    });

    /* battery cell fill: 36 units tall, filled from the bottom */
    var fill = byId('pfFill');
    if (fill) {
      var pct = f.pct == null ? 0 : Math.max(0, Math.min(100, f.pct));
      var h = 36 * pct / 100;
      fill.setAttribute('y', String(232 - h));
      fill.setAttribute('height', String(h));
    }
    setText('pfNL_bat', f.pct == null ? 'Battery' : 'Battery · ' + Math.round(f.pct) + '%');

    var nodeOn = { solar: f.producing, bat: f.charging || f.discharging,
                   grid: f.exporting || f.importing, home: f.load != null && f.load > W_DEAD };
    PF_NODES.forEach(function (n) {
      var el = byId('pfN_' + n.k);
      if (el) {
        el.classList.toggle('is-on', !f.dead && !!nodeOn[n.k]);
        el.classList.toggle('is-chg', n.k === 'bat' && !!f.charging);
      }
    });

    setFig('pv', f.pv == null ? '—' : kwUnit(f.pv),
      f.pv == null ? 'no reading'
        : (f.producing ? (flowLive(f, 'sh') ? kwUnit(f.sh) + ' straight to the house'
                                            : 'all of it going elsewhere')
                       : 'nothing off the roof right now'),
      f.producing);
    setFig('home', f.load == null ? '—' : kwUnit(f.load), homeSources(f), false);
    setFig('bat', f.pct == null ? '—' : Math.round(f.pct) + '%',
      f.charging ? 'charging at ' + kwUnit(f.bat)
        : f.discharging ? 'running the house at ' + kwUnit(f.bat)
        : (f.bat == null && f.pct == null ? 'no reading' : 'resting'),
      f.charging || f.discharging);
    setFig('grid', f.grid == null ? '—' : kwUnit(f.grid),
      f.exporting ? 'exporting' : f.importing ? 'importing' : (f.grid == null ? 'no reading' : 'idle'),
      f.exporting || f.importing);

    var note = byId('pfNote');
    if (note) {
      note.className = 'pf-note' + (f.dead ? ' is-warn' : '');
      note.textContent = f.dead
        ? 'Waiting for the inverter — check the Sungrow integration in Home Assistant.'
        : (off ? 'Home Assistant is offline — this is the last it told us.'
               : (D.ha && D.ha.updated_at ? 'Updated ' + relTime(D.ha.updated_at) : ''));
    }
  }

  function openSolarSheet() {
    var t = solarTile();
    if (!t) return;
    openSheet('<div class="pf" id="pfCard" role="dialog" aria-modal="true" aria-label="Power flow">' +
      solarSheetHtml(t) + '</div>', { raw: true, centre: true });
    state.solar = true;
    paintSolar();
  }

  /* --------------------------------------------------------------- day view */

  function viewDay() {
    var d = state.cursor;
    return '<div class="stack">' + navHeader(fmtLongDate(d), fmtDMY(d)) +
      '<section class="card" style="flex:1 1 auto"><div class="card-head"><h2 class="lbl">Events</h2>' +
      '<span class="spacer"></span><button class="btn sm" data-act="add-event" data-date="' + esc(d) + '">Add event</button></div>' +
      holidayStrip(d) +
      '<div class="scroll">' + scheduleList(d, true) + '</div></section></div>';
  }

  function navHeader(title, sub) {
    return '<div class="hdr"><button class="btn" data-act="nav" data-delta="-1" aria-label="Previous">' + ICON.left + '</button>' +
      '<button class="btn" data-act="nav" data-delta="1" aria-label="Next">' + ICON.right + '</button>' +
      '<h2>' + esc(title) + '</h2><span class="sub mono">' + esc(sub || '') + '</span>' +
      '<span class="spacer"></span>' +
      '<button class="btn ghost" data-act="nav-today">Today</button></div>';
  }

  /* -------------------------------------------------------------- week view */
  /* The owner's ask: "show the events in their correct time slot, not just
     lumped one under the other". Landscape gets a real time grid — one shared
     vertical axis, blocks placed by start and sized by duration. Portrait
     keeps the stacked day cards below. */

  function viewWeek() {
    var mon = mondayOf(state.cursor);
    var sub = fmtDMY(mon) + ' – ' + fmtDMY(addDays(mon, 6));
    return '<div class="stack">' + navHeader('Week of ' + fmtLongDate(mon), sub) +
      (isPortrait() ? weekStacked(mon) : weekGrid(mon)) + '</div>';
  }

  /* Dinner is the one thing the week is planned around, so it keeps its own
     row under every column in both layouts. */
  function weekDinner(d) {
    var dinner = mealLabel(mealFor(d, 'dinner'));
    return '<div class="k">Dinner</div><div class="v">' +
      (dinner ? esc(dinner) : '<span class="muted2">Not planned</span>') + '</div>';
  }

  /* -------- portrait: unchanged stacked day cards, plus holiday chips ---- */

  function weekStacked(mon) {
    var cols = '';
    var now = nowMins();
    for (var i = 0; i < 7; i++) {
      var d = addDays(mon, i);
      var split = splitHolidays(D.byDate[d] || []);
      var evs = split.rest;
      var body = split.hol.map(function (e) { return holidayChip(e, 'wide'); }).join('');
      body += evs.map(function (e) {
        var past = d < state.today || (d === state.today && e.end && !e.all_day && mins(e.end) < now);
        return '<button class="wk-ev' + (past ? ' is-past' : '') + '" data-act="event" data-id="' + esc(e.id) +
          '" style="' + pstyle(e.person_id) + '">' +
          '<span class="t mono">' + esc(e.all_day ? 'All day' : fmtTimeShort(e.start)) + '</span>' +
          '<span class="n">' + esc(e.title) + '</span></button>';
      }).join('');
      if (!body) body = '<div class="muted2" style="font-size:.8125rem;padding:.25rem">—</div>';
      cols += '<div class="wk-col' + (d === state.today ? ' is-today' : '') + '">' +
        '<div class="wk-head"><span class="wd">' + DOW_SHORT[dowOf(d)] + '</span>' +
        '<span class="dd mono">' + pad2(Number(d.split('-')[2])) + '</span></div>' +
        '<div class="wk-body">' + body + '</div>' +
        '<div class="wk-foot">' + weekDinner(d) + '</div></div>';
    }
    return '<div class="week-grid">' + cols + '</div>';
  }

  /* ---------------------------- landscape: the time grid ----------------- */

  /* The axis is 7am–9pm unless the week runs outside it, in which case it
     grows to cover the earliest start and the latest end. Whole hours only —
     a grid whose ticks read 6:40 is a chart, not a calendar. */
  function weekAxis(mon) {
    var startH = 7, endH = 21;
    for (var i = 0; i < 7; i++) {
      (D.byDate[addDays(mon, i)] || []).forEach(function (e) {
        if (e.all_day || !e.start || isHoliday(e)) return;
        var s = mins(e.start);
        var en = e.end ? mins(e.end) : s + 60;
        if (en <= s) en = 24 * 60;                     // runs to midnight
        startH = Math.min(startH, Math.floor(s / 60));
        endH = Math.max(endH, Math.ceil(en / 60));
      });
    }
    startH = Math.max(0, startH);
    endH = Math.min(24, Math.max(endH, startH + 6));
    return { a: startH * 60, b: endH * 60, hours: endH - startH };
  }

  /* Greedy interval colouring, one overlap cluster at a time. Up to three
     events sit side by side; a cluster that needs a fourth lane keeps its
     first two and folds the rest into a "+N" chip that opens the day peek —
     three 5-rem blocks are readable from the kitchen, five are not. */
  function laneOut(items) {
    var out = [], cluster = [], clusterEnd = -1;

    function flush() {
      if (!cluster.length) { clusterEnd = -1; return; }
      var lanes = [];                                  // lane index -> end minute
      cluster.forEach(function (it) {
        var i = 0;
        while (i < lanes.length && lanes[i] > it.s) i++;
        it.lane = i;
        lanes[i] = it.e;
      });
      if (lanes.length <= 3) {
        cluster.forEach(function (it) { it.lanes = lanes.length; out.push(it); });
      } else {
        var spillS = null, spillE = null, spill = 0;
        cluster.forEach(function (it) {
          if (it.lane < 2) { it.lanes = 3; out.push(it); return; }
          spill++;
          if (spillS === null || it.s < spillS) spillS = it.s;
          if (spillE === null || it.e > spillE) spillE = it.e;
        });
        out.push({ more: spill, lane: 2, lanes: 3, s: spillS, e: spillE });
      }
      cluster = [];
      clusterEnd = -1;
    }

    items.forEach(function (it) {
      if (cluster.length && it.s >= clusterEnd) flush();
      cluster.push(it);
      clusterEnd = Math.max(clusterEnd, it.e);
    });
    flush();
    return out;
  }

  function weekBlocks(date, ax) {
    var split = splitHolidays(D.byDate[date] || []);
    var now = nowMins();
    var items = [];
    split.rest.forEach(function (e) {
      if (e.all_day || !e.start) return;
      var s = mins(e.start);
      var en = e.end ? mins(e.end) : s + 60;
      if (en <= s) en = 24 * 60;
      items.push({ ev: e, s: s, e: en });
    });
    items.sort(function (x, y) { return x.s - y.s || (y.e - y.s) - (x.e - x.s); });

    var span = ax.b - ax.a;
    return laneOut(items).map(function (it) {
      var top = ((Math.max(ax.a, it.s) - ax.a) / span) * 100;
      var hgt = ((Math.min(ax.b, it.e) - Math.max(ax.a, it.s)) / span) * 100;
      var w = 100 / it.lanes;
      var pos = 'top:' + top.toFixed(3) + '%;height:' + Math.max(hgt, 1.4).toFixed(3) +
        '%;left:calc(' + (it.lane * w).toFixed(3) + '% + 1px);width:calc(' + w.toFixed(3) + '% - 3px)';
      if (it.more) {
        return '<button class="wg-more" data-act="peek-day" data-date="' + esc(date) +
          '" style="' + pos + '"><span class="mono">+' + it.more + '</span></button>';
      }
      var e = it.ev;
      var dur = it.e - it.s;
      var past = date < state.today || (date === state.today && it.e < now);
      return '<button class="wg-ev' + (past ? ' is-past' : '') + (dur < 40 ? ' is-tiny' : '') +
        (dur < 75 ? ' is-short' : '') + (it.lanes > 2 ? ' is-narrow' : '') +
        '" data-act="event" data-id="' + esc(e.id) +
        '" style="' + pstyle(e.person_id) + ';' + pos + '">' +
        '<span class="wg-bar"></span>' +
        '<span class="wg-n">' + esc(e.title) + '</span>' +
        '<span class="wg-t mono">' + esc(fmtTimeShort(e.start)) +
        (e.end ? '–' + esc(fmtTimeShort(e.end)) : '') + '</span></button>';
    }).join('');
  }

  function weekGrid(mon) {
    var ax = weekAxis(mon);
    var span = ax.b - ax.a;
    var now = nowMins();

    var head = '<div class="wg-gut"></div>', allday = '<div class="wg-gut lbl">All day</div>';
    var cols = '<div class="wg-axis">', foot = '<div class="wg-gut lbl">Dinner</div>';

    for (var h = ax.a; h <= ax.b; h += 60) {
      var pct = ((h - ax.a) / span) * 100;
      if (h === ax.b) continue;                        // the closing hour needs no label
      cols += '<div class="wg-tick mono" style="top:' + pct.toFixed(3) + '%">' +
        esc(fmtTimeShort(pad2(Math.floor(h / 60)) + ':00')) + '</div>';
    }
    cols += '</div>';

    for (var i = 0; i < 7; i++) {
      var d = addDays(mon, i);
      var isToday = d === state.today;
      var split = splitHolidays(D.byDate[d] || []);

      head += '<div class="wg-hd' + (isToday ? ' is-today' : '') + '">' +
        '<span class="wd">' + DOW_SHORT[dowOf(d)] + '</span>' +
        '<span class="dd mono">' + pad2(Number(d.split('-')[2])) + '</span></div>';

      var chips = split.hol.map(function (e) { return holidayChip(e); }).join('');
      chips += split.rest.filter(function (e) { return e.all_day; }).map(function (e) {
        return '<button class="wg-ad" data-act="event" data-id="' + esc(e.id) +
          '" style="' + pstyle(e.person_id) + '">' + esc(e.title) + '</button>';
      }).join('');
      allday += '<div class="wg-adc' + (isToday ? ' is-today' : '') + '">' + chips + '</div>';

      var nowLine = '';
      if (isToday && now >= ax.a && now <= ax.b) {
        var nowPct = ((now - ax.a) / span) * 100;
        /* The base position is set once in `top`; every crawl after that is a
           transform off this base, so the tick never relays out the column. */
        nowLine = '<div class="wg-now" data-base="' + nowPct.toFixed(3) +
          '" style="top:' + nowPct.toFixed(3) + '%"><span class="wg-now-k"></span></div>';
      }
      cols += '<div class="wg-col' + (isToday ? ' is-today' : '') + '">' +
        weekBlocks(d, ax) + nowLine + '</div>';

      foot += '<div class="wg-ftc' + (isToday ? ' is-today' : '') + '">' + weekDinner(d) + '</div>';
    }

    return '<div class="wg" data-a="' + ax.a + '" data-b="' + ax.b + '" style="--wg-hrs:' + ax.hours + '">' +
      '<div class="wg-head">' + head + '</div>' +
      '<div class="wg-allday">' + allday + '</div>' +
      '<div class="wg-body"><div class="wg-canvas">' + cols + '</div></div>' +
      '<div class="wg-foot">' + foot + '</div></div>';
  }

  /* A week with a 5 am start and an 11 pm finish is taller than the stage, so
     the first paint of that week puts the now line in the middle rather than
     leaving the panel showing the small hours. Only the first paint — after
     that the reader's own scroll position is theirs to keep. */
  function centreWeekOnNow() {
    if (state.wgScrolled) return;
    var body = document.querySelector('.wg-body');
    if (!body) return;
    var over = body.scrollHeight - body.clientHeight;
    if (over <= 2) { state.wgScrolled = true; return; }
    var line = body.querySelector('.wg-now');
    var top = line
      ? line.offsetTop - body.clientHeight / 2
      : 0;
    body.scrollTop = Math.max(0, Math.min(over, top));
    state.wgScrolled = true;
  }

  /* The now line crawls on the same 30 s tick as the rail spine rather than
     waiting for the next full render. */
  function renderWeekNow() {
    var wg = document.querySelector('.wg');
    if (!wg) return;
    var line = wg.querySelector('.wg-now');
    var a = Number(wg.dataset.a), b = Number(wg.dataset.b), now = nowMins();
    if (!line) return;
    if (now < a || now > b) { line.style.display = 'none'; return; }
    line.style.display = '';
    var pct = ((now - a) / (b - a)) * 100;
    var base = Number(line.dataset.base);
    var col = line.parentNode;
    var h = col ? col.clientHeight : 0;
    if (!h || isNaN(base)) { line.style.top = pct.toFixed(3) + '%'; return; }
    line.style.transform = 'translateY(' + (((pct - base) / 100) * h).toFixed(1) + 'px)';
  }

  /* ------------------------------------------------------------- month view */

  function viewMonth() {
    var p = state.cursor.split('-').map(Number);
    var first = p[0] + '-' + pad2(p[1]) + '-01';
    var start = mondayOf(first);
    var cells = '';
    for (var i = 0; i < 42; i++) {
      var d = addDays(start, i);
      var out = Number(d.split('-')[1]) !== p[1];
      var split = splitHolidays(D.byDate[d] || []);
      var evs = split.rest;
      /* the holiday reads as a thin labelled band across the cell and does
         not eat one of the four pills the day gets */
      var shown = split.hol.map(function (e) {
        return '<div class="mo-hol" data-act="event" data-id="' + esc(e.id) + '">' +
          '<span class="g">' + ICON.flag + '</span>' + esc(e.title) + '</div>';
      }).join('');
      shown += evs.slice(0, 4).map(function (e) {
        return '<div class="mo-ev" style="' + pstyle(e.person_id) + '">' +
          (e.all_day ? '' : '<span class="mono">' + esc(fmtTimeShort(e.start)) + '</span> ') +
          esc(e.title) + '</div>';
      }).join('');
      if (evs.length > 4) shown += '<div class="mo-more">+' + (evs.length - 4) + ' more</div>';
      cells += '<button class="mo-cell' + (out ? ' is-out' : '') + (d === state.today ? ' is-today' : '') +
        '" data-act="peek-day" data-date="' + d + '">' +
        '<span class="mo-n mono">' + Number(d.split('-')[2]) + '</span>' + shown + '</button>';
    }
    var dow = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
      .map(function (x) { return '<div>' + x + '</div>'; }).join('');
    /* is-month lets the portrait stylesheet stretch the grid down the page —
       six rows of squares that stop half way up a phone look broken. */
    return '<div class="stack is-month">' + navHeader(MON_LONG[p[1] - 1] + ' ' + p[0], '') +
      '<div class="mo-dow">' + dow + '</div><div class="mo-grid">' + cells + '</div></div>';
  }

  /* A month cell holds four pills at most, so the rest of the day has to live
     somewhere: the first tap opens a peek beside the cell, and only "Open day"
     leaves the month behind. */

  function openDayPeek(date, anchor) {
    var split = splitHolidays(D.byDate[date] || []);
    var evs = split.rest;
    var body = split.hol.map(function (e) { return holidayChip(e, 'wide'); }).join('');
    body += evs.map(function (e) {
      return '<button class="peek-ev" data-act="event" data-id="' + esc(e.id) +
        '" style="' + pstyle(e.person_id) + '">' +
        '<span class="dot"></span>' +
        '<span class="t mono">' + esc(e.all_day ? 'All day' : fmtTime(e.start)) + '</span>' +
        '<span class="n">' + esc(e.title) + '</span></button>';
    }).join('');
    if (!body) body = '<div class="empty" style="padding:.75rem 0">Nothing on — enjoy the quiet.</div>';

    openSheet('<div class="peek" id="peekCard" role="dialog" aria-modal="true">' +
      '<div class="peek-head"><div><div class="peek-dow">' + esc(fmtLongDate(date)) + '</div>' +
      '<div class="peek-date mono">' + esc(fmtDMY(date)) + '</div></div><span class="spacer"></span>' +
      '<button class="close-x" data-act="close-sheet" aria-label="Close">' + ICON.close + '</button></div>' +
      '<div class="peek-body">' + body + '</div>' +
      '<div class="peek-foot"><button class="btn sm primary" data-act="open-day" data-date="' + esc(date) +
      '">Open day</button><span style="flex:1 1 auto"></span>' +
      '<button class="btn sm" data-act="add-event" data-date="' + esc(date) + '">Add event</button></div></div>',
      { raw: true, cls: 'plain' });
    positionPeek(anchor);
  }

  /* Sit under the cell where there is room, above it where there isn't, and
     never off the edge of the screen. */
  function positionPeek(anchor) {
    var el = byId('peekCard');
    if (!el) return;
    var pad = 12;
    var w = el.offsetWidth, h = el.offsetHeight;
    var vw = window.innerWidth, vh = window.innerHeight;
    var left = (vw - w) / 2, top = (vh - h) / 2;
    var r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
    if (r && r.width) {
      left = r.left + r.width / 2 - w / 2;
      top = r.bottom + 8;
      if (top + h > vh - pad) top = r.top - h - 8;
    }
    el.style.left = Math.max(pad, Math.min(left, vw - w - pad)) + 'px';
    el.style.top = Math.max(pad, Math.min(top, vh - h - pad)) + 'px';
  }

  /* ----------------------------------------------------------- chores view - */

  function viewChores() {
    var ch = D.chores;
    if (!ch || !ch.people) {
      return '<div class="stack"><div class="hdr"><h2>Chores</h2></div>' +
        '<div class="empty">' + waitingCopy('Chores') + '</div></div>';
    }
    var cols = ch.people.map(function (p) {
      var body = (p.chores && p.chores.length)
        ? p.chores.map(function (c) { return choreRow(c, p.id, false); }).join('')
        : '<div class="empty">No chores today — nice one.</div>';
      return '<section class="card person-card" style="' + pstyle(p.id) + '"><div class="card-head">' +
        '<span class="who" style="font-size:1.125rem;font-weight:700;display:flex;align-items:center;gap:.5rem">' +
        personChip(p.id) + '</span>' +
        '<span class="spacer"></span>' + starPips(p.stars_week || 0, 10, true) + '</div>' +
        '<div class="scroll">' + body + '</div>' +
        '<div style="display:flex;gap:.5rem;margin-top:.75rem;padding-top:.75rem;border-top:1px solid var(--hair-2)">' +
        '<button class="btn sm" data-act="redeem" data-person="' + esc(p.id) + '">Redeem stars</button></div>' +
        '</section>';
    }).join('');
    return '<div class="stack"><div class="hdr"><h2>Chores</h2>' +
      '<span class="sub mono">' + esc(fmtDMY(state.today)) + '</span><span class="spacer"></span>' +
      '<button class="btn" data-act="manage-chores">Manage chores</button></div>' +
      '<div class="cols" style="grid-template-columns:repeat(auto-fit,minmax(15rem,1fr))">' +
      cols + '</div></div>';
  }

  /* ------------------------------------------------------------ lists view - */

  function viewLists() {
    var ls = (D.lists && D.lists.lists) || [];
    if (!ls.length) {
      return '<div class="stack"><div class="hdr"><h2>Lists</h2><span class="spacer"></span>' +
        '<button class="btn" data-act="new-list">New list</button></div>' +
        '<div class="empty">No lists yet — make one and start adding.</div></div>';
    }
    var cols = ls.map(function (l) {
      var items = (l.items || []).slice().sort(function (a, b) {
        if (!!a.done !== !!b.done) return a.done ? 1 : -1;
        return (a.id || 0) - (b.id || 0);
      });
      var body = items.length ? items.map(function (it) {
        return '<div class="item' + (it.done ? ' is-done' : '') + '" data-act="item" data-id="' + esc(it.id) +
          '" data-long="item" data-text="' + esc(it.text) + '" role="button" tabindex="0">' +
          '<span class="box">' + ICON.tick + '</span>' +
          '<span class="item-text">' + esc(it.text) + '</span></div>';
      }).join('') : '<div class="empty">Empty — add the first thing.</div>';
      /* Groceries and To-do are the panel's furniture — only lists the family
         made themselves can be held down and removed. */
      var own = l.kind !== 'grocery' && l.kind !== 'todo';
      return '<section class="card"><div class="card-head"><h2 class="lbl' + (own ? ' holdable' : '') + '"' +
        (own ? ' data-long="list" data-id="' + esc(l.id) + '" data-name="' + esc(l.name) + '"' : '') +
        '>' + esc(l.name) + (own ? '<span class="hold-hint">hold to delete</span>' : '') + '</h2>' +
        '<span class="spacer"></span><span class="muted2" style="font-size:.75rem">' +
        items.filter(function (i) { return !i.done; }).length + ' left</span></div>' +
        '<form class="list-add" data-act="add-item" data-list="' + esc(l.id) + '">' +
        '<input class="inp" type="text" placeholder="Add to ' + esc(l.name.toLowerCase()) + '" ' +
        'data-keep="list-' + esc(l.id) + '" autocomplete="off" enterkeyhint="done">' +
        '<button class="btn primary" type="submit" aria-label="Add">' + ICON.plus + '</button></form>' +
        '<div class="scroll">' + body + '</div></section>';
    }).join('');
    return '<div class="stack"><div class="hdr"><h2>Lists</h2>' +
      '<span class="sub">Tick to cross off · hold an item, or your own list’s name, to delete</span>' +
      '<span class="spacer"></span><button class="btn" data-act="new-list">New list</button></div>' +
      '<div class="cols" style="grid-template-columns:repeat(auto-fit,minmax(19rem,1fr))">' +
      cols + '</div></div>';
  }

  /* ------------------------------------------------------------ meals view - */

  function viewMeals() {
    var mp = D.mealplan;
    var mon = (mp && mp.week_start) || mondayOf(state.cursor);
    var days = [];
    for (var i = 0; i < 7; i++) days.push(addDays(mon, i));

    var heads = '<div class="meal-rowlbl"></div>' + days.map(function (d) {
      return '<div class="meal-hd' + (d === state.today ? ' is-today' : '') + '">' +
        '<div class="wd">' + DOW_SHORT[dowOf(d)] + '</div>' +
        '<div class="dd mono">' + pad2(Number(d.split('-')[2])) + '</div></div>';
    }).join('');

    function ingredientsOf(m) {
      if (!m || !m.recipe_id) return null;
      var r = recipeById(m.recipe_id);
      return r && r.ingredients && r.ingredients.length ? r.ingredients : null;
    }

    function row(slot, cls, label) {
      return '<div class="meal-rowlbl"><span class="lbl">' + label + '</span></div>' +
        days.map(function (d) {
          var m = mealFor(d, slot);
          var name = mealLabel(m);
          var hasRecipe = m && m.recipe_id;
          var ing = slot === 'dinner' ? ingredientsOf(m) : null;
          var rec = hasRecipe ? recipeById(m.recipe_id) : null;
          var chips = (rec && slot === 'dinner') ? recipeChips(rec, { quiet: true, noSource: true }) : '';
          return '<div class="meal-cell ' + cls + (hasRecipe ? ' has-recipe' : '') +
            (d === state.today ? ' is-today' : '') + '">' +
            '<button class="meal-pick" data-act="meal" data-date="' + d + '" data-slot="' + slot + '">' +
            /* portrait stacks the grid one day per row, so each cell has to
               name its own day — hidden under the column headings otherwise */
            '<span class="meal-day">' + DOW_SHORT[dowOf(d)] + ' ' +
            pad2(Number(d.split('-')[2])) + '</span>' +
            (name ? '<span class="n' + (hasRecipe ? '' : ' free') + '">' + esc(name) + '</span>'
                  : '<span class="ph">Tap to plan</span>') +
            (chips ? '<span class="meal-meta">' + chips + '</span>' : '') +
            (ing ? '<span class="ing">' + esc(ing.join(' · ')) + '</span>' : '') +
            '</button>' +
            (hasRecipe && slot === 'dinner'
              ? '<button class="push" data-act="push-groceries" data-date="' + d + '" data-slot="dinner">' +
                '+ groceries</button>' : '') +
            '</div>';
        }).join('');
    }

    var rows = row('dinner', 'dinner', 'Dinner');
    var tmpl = 'auto minmax(13rem, 1fr)';
    if (state.showOtherMeals) {
      rows += row('lunch', 'small', 'Lunch') + row('other', 'small', 'Other');
      tmpl = 'auto minmax(11rem, 1fr) 6rem 6rem';
    }
    var sub = fmtDMY(mon) + ' – ' + fmtDMY(addDays(mon, 6));

    return '<div class="stack">' +
      '<div class="hdr"><button class="btn" data-act="nav" data-delta="-1" aria-label="Previous week">' + ICON.left + '</button>' +
      '<button class="btn" data-act="nav" data-delta="1" aria-label="Next week">' + ICON.right + '</button>' +
      '<h2>Meals</h2><span class="sub mono">' + esc(sub) + '</span><span class="spacer"></span>' +
      '<button class="btn" data-act="toggle-meals">' + (state.showOtherMeals ? 'Hide' : 'Show') + ' lunch &amp; other</button>' +
      '<button class="btn accent" data-act="recipes">Recipes</button>' +
      '<button class="btn ghost" data-act="nav-today">This week</button></div>' +
      '<div class="meal-grid" style="grid-template-rows:' + tmpl + '">' + heads + rows + '</div>' +
      '<p class="muted2" style="margin:1rem 0;font-size:.875rem">Tap a night to plan it. ' +
      'Recipes can push their ingredients straight onto the grocery list.</p>' +
      groceryStrip() + '</div>';
  }

  /* Compact grocery list under the meal grid — planning and shopping in one place. */
  function groceryStrip() {
    var ls = (D.lists && D.lists.lists) || [];
    var g = ls.filter(function (l) { return l.kind === 'grocery'; })[0] || ls[0];
    if (!g) return '';
    var open = (g.items || []).filter(function (i) { return !i.done; });
    return '<section class="card" style="flex:0 0 auto;max-height:16rem"><div class="card-head">' +
      '<h2 class="lbl">' + esc(g.name) + '</h2><span class="spacer"></span>' +
      '<span class="muted2" style="font-size:.75rem">' + plural(open.length, 'thing', 'things') + ' to get</span></div>' +
      (open.length
        ? '<div class="scroll item-grid">' + open.map(function (it) {
            return '<div class="item" data-act="item" data-id="' + esc(it.id) +
              '" data-long="item" data-text="' + esc(it.text) + '" role="button" tabindex="0">' +
              '<span class="box">' + ICON.tick + '</span>' +
              '<span class="item-text">' + esc(it.text) + '</span></div>';
          }).join('') + '</div>'
        : '<div class="empty">Nothing to get — the list is clear.</div>') +
      '</section>';
  }

  /* ------------------------------------------------------------- rendering - */

  function viewHtml() {
    switch (state.view) {
      case 'day': return viewDay();
      case 'week': return viewWeek();
      case 'month': return viewMonth();
      case 'chores': return viewChores();
      case 'lists': return viewLists();
      case 'meals': return viewMeals();
      default: return viewToday();
    }
  }

  var camTimer = null;

  /* ---------------------------------------------------------- scheduling -- */
  /* Every poll used to call render() the moment its answer landed, so a 60 s
     tick chain rebuilt the whole view three times over and a tab switch up to
     five. One dirty flag and one frame-deferred pass collapses all of that
     into a single rebuild.

     Three things hold a rebuild off, and all three set the flag instead of
     dropping the work: a finger in a text field (the caret and the soft
     keyboard belong to the reader), a tile waiting for its confirm tap, and
     the panel being behind the sleep overlay or the picture frame. Each of
     those has a wake-up that flushes the flag, so nothing waits forever. */

  var renderDirty = false;
  var renderQueued = false;

  /* Behind the sleep overlay or the screensaver, nothing on the wall is
     visible — rebuilding the view all night is pure heat. */
  function panelDark() {
    var sl = byId('sleep');
    return !!(SV && SV.on) || !!(sl && !sl.hidden);
  }

  function typingInView() {
    var a = document.activeElement;
    if (!a || (a.tagName !== 'INPUT' && a.tagName !== 'TEXTAREA')) return false;
    var v = byId('view');
    return !!(v && v.contains(a));
  }

  function armedTile() {
    return !!document.querySelector('.t-btn[data-armed="1"]');
  }

  /* force = the rebuild was asked for by a tap, so only the dark-panel and
     armed-confirm holds apply; a poll additionally yields to typing. */
  function scheduleRender(force) {
    renderDirty = true;
    if (renderQueued) return;
    renderQueued = true;
    var ran = false;
    var run = function () {
      if (ran) return;
      ran = true;
      renderQueued = false;
      if (!renderDirty) return;
      if (panelDark() || armedTile()) return;          // stays dirty
      if (!force && typingInView()) return;            // stays dirty
      renderDirty = false;
      render();
    };
    if (window.requestAnimationFrame) window.requestAnimationFrame(run);
    /* A frame callback never arrives while the tablet's screen is off, and a
       queue that never drains is a queue that never accepts another render —
       the timer is the floor under it. Whichever lands first wins. */
    setTimeout(run, 250);
  }

  function renderSoon() { scheduleRender(true); }      // after a tap
  function renderPoll() { scheduleRender(false); }     // after a timer

  /* Whatever was held off — a blur, a sheet closing, a confirm resolving, the
     wall waking up — comes through here. */
  function renderCatchUp() {
    if (renderDirty) scheduleRender(true);
  }

  function render() {
    try {
      renderNow();
      failStreak = 0;
    } catch (err) {
      renderDirty = true;
      panelFailure('render', err);
    }
  }

  function renderNow() {
    var v = byId('view');
    if (!v) return;
    /* Never redraw out from under a button waiting for its second tap — the
       15 s tile poll used to wipe the confirm mid-gesture. */
    if (armedTile()) { renderDirty = true; return; }
    var keep = {};
    Array.prototype.forEach.call(v.querySelectorAll('[data-keep]'), function (el) {
      keep[el.dataset.keep] = {
        v: el.value,
        f: document.activeElement === el,
        s: el.selectionStart,
        e: el.selectionEnd
      };
    });
    var sels = '.scroll,.wk-body,.wg-body';
    var scrolls = Array.prototype.map.call(v.querySelectorAll(sels), function (e) { return e.scrollTop; });
    /* The device shelf scrolls sideways, and the tile poll re-renders this
       whole view every 15 s — without carrying its scrollLeft across, a
       swipe silently snapped back to the first tile a few seconds later,
       which reads as "it doesn't scroll at all". */
    var shelf = v.querySelector('.ha-row');
    var shelfX = shelf ? shelf.scrollLeft : 0;

    v.innerHTML = viewHtml();

    Array.prototype.forEach.call(v.querySelectorAll('[data-keep]'), function (el) {
      var k = keep[el.dataset.keep];
      if (!k) return;
      el.value = k.v;
      if (!k.f) return;
      el.focus();
      /* .focus() alone drops the caret at the end, which on a tablet reads as
         the panel eating your place in the word — put it back. */
      if (k.s != null && el.setSelectionRange) {
        try { el.setSelectionRange(k.s, k.e == null ? k.s : k.e); } catch (e) { }
      }
    });
    Array.prototype.forEach.call(v.querySelectorAll(sels), function (e, i) {
      if (scrolls[i] != null) e.scrollTop = scrolls[i];
    });
    centreWeekOnNow();
    /* the chores list only wears its fade-out when there is more below it */
    var cs = v.querySelector('.a-chores .scroll');
    if (cs) cs.classList.toggle('can-scroll', cs.scrollHeight - cs.clientHeight > 2);
    var shelf2 = v.querySelector('.ha-row');
    if (shelf2 && shelfX) shelf2.scrollLeft = shelfX;
    paintShelf();
    renderRail();
    updateNowPlaying();
    updateClimateSheet();
    paintSolar();
  }

  /* Which way the shelf still has tiles in. Drives the edge fades: on a
     narrower panel three of nine tiles sit past the right edge, and with no
     fade the wall just looks like it owns fewer controls than it does. */
  function paintShelf() {
    var row = document.querySelector('.ha-row');
    if (!row) return;
    var more = row.scrollWidth - row.clientWidth;
    row.classList.toggle('can-x', more > 2);
    row.classList.toggle('at-start', row.scrollLeft <= 2);
    row.classList.toggle('at-end', row.scrollLeft >= more - 2);
  }

  /* scroll doesn't bubble, so listen in the capture phase */
  document.addEventListener('scroll', function (e) {
    var t = e.target;
    if (t && t.classList && t.classList.contains('ha-row')) paintShelf();
  }, true);

  /* A mouse or a trackpad only ever sends vertical deltas over a shelf like
     this one, so turn them sideways — otherwise every tile past the edge is
     unreachable without a touchscreen. */
  document.addEventListener('wheel', function (e) {
    var row = e.target && e.target.closest ? e.target.closest('.ha-row') : null;
    if (!row) return;
    if (row.scrollWidth - row.clientWidth < 2) return;
    if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;   // already sideways
    row.scrollLeft += e.deltaY;
    e.preventDefault();
  }, { passive: false });

  window.addEventListener('resize', paintShelf);

  function refreshCameras() {
    camStamp++;
    Array.prototype.forEach.call(document.querySelectorAll('img[data-cam]'), function (img) {
      var base = img.getAttribute('src') || '';
      if (base.indexOf('data:') === 0) return;
      img.src = camBust(base);
    });
  }

  /* ---------------------------------------------------------------- sheets - */

  function closeSheet() {
    var host = byId('sheetHost');
    assistRelease();
    host.innerHTML = '';
    state.np = null;
    state.climate = false;
    state.solar = false;
    stopStatusRefresh();
    renderCatchUp();
  }

  function openSheet(html, opts) {
    opts = opts || {};
    var host = byId('sheetHost');
    assistRelease();
    state.np = null;
    state.climate = false;
    state.solar = false;
    stopStatusRefresh();
    host.innerHTML = '<div class="scrim' + (opts.centre ? ' centre' : '') +
      (opts.cls ? ' ' + opts.cls : '') + '" data-act="scrim">' +
      (opts.raw ? html :
        '<div class="sheet' + (opts.wide ? ' wide' : '') + '" role="dialog" aria-modal="true">' +
        '<div class="sheet-head"><h2>' + esc(opts.title || '') + '</h2><span class="spacer"></span>' +
        (opts.headExtra || '') +
        '<button class="close-x" data-act="close-sheet" aria-label="Close">' + ICON.close + '</button></div>' +
        '<div class="sheet-body">' + html + '</div>' +
        (opts.foot ? '<div class="sheet-foot">' + opts.foot + '</div>' : '') +
        '</div>') +
      '</div>';
    var first = host.querySelector('.sheet-body input[type=text], .sheet-body input[type=password]');
    if (first && opts.focus !== false) first.focus();
  }

  function findEvent(id) {
    var evs = (D.agenda && D.agenda.events) || [];
    for (var i = 0; i < evs.length; i++) if (String(evs[i].id) === String(id)) return evs[i];
    return null;
  }

  function openEventDetail(id) {
    var e = findEvent(id);
    if (!e) { toast('That event is no longer in view.'); return; }

    /* A public holiday has nothing to edit and nobody to assign — the sheet
       says what it is, which day it falls on and which feed it came off. */
    if (isHoliday(e)) {
      openSheet('<h3 class="hol-h">' + esc(e.title) + '</h3>' +
        detailRow('When', fmtLongDate(e.date) + ' · ' + fmtDMY(e.date)) +
        detailRow('What', 'Public holiday') +
        detailRow('Calendar', e.feed || 'Holidays') +
        '<div class="note">Public holidays are read-only — they come from the ' +
        '<b>' + esc(e.feed || 'Holidays') + '</b> feed.</div>',
        {
          title: 'Public holiday', focus: false,
          foot: '<span style="flex:1 1 auto"></span>' +
            '<button class="btn ghost" data-act="close-sheet">Close</button>'
        });
      return;
    }

    var rows = '';
    var when = e.all_day
      ? 'All day' + (e.end_date && e.end_date !== e.date ? ' · ' + fmtDMY(e.date) + ' – ' + fmtDMY(e.end_date) : '')
      : fmtTime(e.start) + (e.end ? ' – ' + fmtTime(e.end) : '');
    rows += detailRow('When', fmtLongDate(e.date) + ' · ' + fmtDMY(e.date));
    rows += detailRow('Time', when);
    if (e.location) rows += detailRow('Where', e.location);
    rows += detailRow('Who', e.person_id ? personChip(e.person_id) : 'Not assigned', true);
    if (e.notes) rows += detailRow('Notes', e.notes);
    if (e.source === 'ics') rows += detailRow('Calendar', e.feed || 'Google');

    /* Name the feed the event came off — "the Work calendar" is the answer to
       the question people actually ask when an event looks wrong. */
    var note = e.editable === false || e.source === 'ics'
      ? '<div class="note">From the <b>' + esc(e.feed || 'Google') + '</b> calendar — edit it there ' +
        'and it will update here within 5 minutes.</div>'
      : '';
    var foot = (e.editable !== false && e.source !== 'ics')
      ? '<button class="btn primary" data-act="edit-event" data-id="' + esc(e.id) + '">Edit</button>' +
        '<button class="btn danger" data-act="delete-event" data-id="' + esc(e.id) + '">Delete</button>' +
        '<span style="flex:1 1 auto"></span><button class="btn ghost" data-act="close-sheet">Close</button>'
      : '<span style="flex:1 1 auto"></span><button class="btn ghost" data-act="close-sheet">Close</button>';

    openSheet('<h3 style="font-size:1.75rem;font-weight:700;line-height:1.15;margin-bottom:1rem">' +
      esc(e.title) + '</h3>' + rows + note, { title: 'Event', foot: foot, focus: false });
  }

  function detailRow(k, v, raw) {
    return '<div class="detail-row"><div class="k">' + esc(k) + '</div><div class="v">' +
      (raw ? v : esc(v)) + '</div></div>';
  }

  /* 'family' is the natural owner for a bin night or a payday, but it is just
     a row in `people` that anyone is free to rename or delete — and an event
     assigned to an id that no longer exists gets no colour, no name and no
     place in the who's-busy bar. Fall back to whoever actually exists. */
  function defaultPersonId() {
    var ppl = (D.status && D.status.config && D.status.config.people) || [];
    for (var i = 0; i < ppl.length; i++) if (ppl[i].id === 'family') return 'family';
    return ppl.length ? ppl[0].id : 'family';
  }

  function personPicker(selected, name) {
    return '<div class="pick">' + people().map(function (p) {
      return '<button type="button" class="pick-btn' + (p.id === selected ? ' is-on' : '') +
        '" data-act="pick-person" data-name="' + esc(name) + '" data-value="' + esc(p.id) + '">' +
        '<span class="dot" style="background:' + esc(colourOf(p.id)) + '"></span>' + esc(p.name) + '</button>';
    }).join('') + '<input type="hidden" id="' + esc(name) + '" value="' + esc(selected || '') + '"></div>';
  }

  function openEventForm(ev, date) {
    var e = ev || {
      title: '', person_id: defaultPersonId(), date: date || state.today, all_day: false,
      start: '09:00', end: '10:00', location: '', notes: '', countdown: false
    };
    var body =
      '<div class="field"><label for="f-title">Title</label>' +
      '<input class="inp" id="f-title" type="text" value="' + esc(e.title) + '" placeholder="What is it?"></div>' +
      '<div class="field"><label>Who</label>' + personPicker(e.person_id || defaultPersonId(), 'f-person') + '</div>' +
      '<div class="field-row' + (e.all_day ? ' is-allday' : '') + '" id="f-when">' +
      '<div class="field"><label for="f-date">Date</label>' +
      '<input class="inp mono" id="f-date" type="date" value="' + esc(e.date) + '">' +
      '<div class="hint mono" id="f-date-hint">' + esc(fmtDMY(e.date)) + '</div></div>' +
      '<div class="field timed"><label for="f-start">Start</label>' +
      '<input class="inp mono" id="f-start" type="time" value="' + esc(e.start || '09:00') + '"></div>' +
      '<div class="field timed"><label for="f-end">End</label>' +
      '<input class="inp mono" id="f-end" type="time" value="' + esc(e.end || '10:00') + '"></div>' +
      '</div>' +
      '<div class="field"><button type="button" class="switch' + (e.all_day ? ' is-on' : '') +
      '" data-act="toggle" data-name="f-allday"><span class="track"></span>All day' +
      '<input type="hidden" id="f-allday" value="' + (e.all_day ? '1' : '') + '"></button></div>' +
      '<div class="field"><label for="f-location">Where</label>' +
      '<input class="inp" id="f-location" type="text" value="' + esc(e.location || '') + '" placeholder="Optional"></div>' +
      '<div class="field"><label for="f-notes">Notes</label>' +
      '<textarea class="inp" id="f-notes" placeholder="Optional">' + esc(e.notes || '') + '</textarea></div>' +
      '<div class="field"><button type="button" class="switch' + (e.countdown ? ' is-on' : '') +
      '" data-act="toggle" data-name="f-countdown"><span class="track"></span>Count the sleeps on Today' +
      '<input type="hidden" id="f-countdown" value="' + (e.countdown ? '1' : '') + '"></button></div>';

    openSheet(body, {
      title: ev ? 'Edit event' : 'Add event',
      foot: '<button class="btn primary" data-act="save-event"' + (ev ? ' data-id="' + esc(ev.id) + '"' : '') +
        '>Save</button><button class="btn ghost" data-act="close-sheet">Cancel</button>'
    });
  }

  function readForm(id) { var el = byId(id); return el ? el.value : ''; }

  function saveEvent(id) {
    var body = {
      title: readForm('f-title').trim(),
      person_id: readForm('f-person') || null,
      date: readForm('f-date'),
      all_day: readForm('f-allday') === '1',
      location: readForm('f-location').trim() || null,
      notes: readForm('f-notes').trim() || null,
      countdown: readForm('f-countdown') === '1'
    };
    if (!body.title) { toast('Give the event a title first.'); return; }
    if (!body.date) { toast('Pick a date first.'); return; }
    if (body.all_day) { body.start = null; body.end = null; }
    else { body.start = readForm('f-start'); body.end = readForm('f-end'); }

    var p = id ? PATCH('/api/events/' + id, body) : POST('/api/events', body);
    act(p, function () {
      closeSheet();
      toast(id ? 'Event updated.' : 'Event added.');
      loadAgenda().then(renderSoon);
    });
  }

  function deleteEvent(id) {
    confirmDialog('Delete this event?', 'It will be removed from the panel.', 'Delete').then(function (ok) {
      if (!ok) return;
      act(DEL('/api/events/' + id), function () {
        closeSheet();
        toast('Event deleted.');
        loadAgenda().then(renderSoon);
      });
    });
  }

  function confirmDialog(title, msg, label) {
    return new Promise(function (resolve) {
      var host = byId('sheetHost');
      var prev = host.innerHTML;
      host.innerHTML = '<div class="scrim centre"><div class="sheet" style="width:min(30rem,90vw);height:auto;border:1px solid var(--hair);border-radius:var(--radius)">' +
        '<div class="sheet-head"><h2>' + esc(title) + '</h2></div>' +
        '<div class="sheet-body"><p style="font-size:1rem;color:var(--ink-2)">' + esc(msg) + '</p></div>' +
        '<div class="sheet-foot"><button class="btn primary" id="cfm-yes">' + esc(label || 'Confirm') + '</button>' +
        '<button class="btn ghost" id="cfm-no">Cancel</button></div></div></div>';
      byId('cfm-yes').addEventListener('click', function () { host.innerHTML = prev; resolve(true); });
      byId('cfm-no').addEventListener('click', function () { host.innerHTML = prev; resolve(false); });
    });
  }

  /* -------------------------------------------------------------- PIN gate - */

  function openPin(msg, opts) {
    opts = opts || {};
    var pad = '';
    [1, 2, 3, 4, 5, 6, 7, 8, 9].forEach(function (n) {
      pad += '<button class="btn" style="min-height:4rem;font-size:1.5rem;font-family:var(--mono)" data-act="pin-key" data-key="' + n + '">' + n + '</button>';
    });
    /* The pad shakes once when a PIN comes back rejected; under
       prefers-reduced-motion the same class flashes a warn border instead. */
    pad = '<div class="pinpad' + (opts.shake ? ' is-wrong' : '') + '" id="pinPad">' + pad +
      '<button class="btn" data-act="pin-key" data-key="clear">Clear</button>' +
      '<button class="btn" style="min-height:4rem;font-size:1.5rem;font-family:var(--mono)" data-act="pin-key" data-key="0">0</button>' +
      '<button class="btn" data-act="pin-key" data-key="del">Back</button></div>';
    openSheet('<p class="' + (opts.shake ? 'pin-wrong' : 'muted') + '" style="margin-bottom:1rem">' +
      esc(msg || 'Enter the admin PIN to change chores or redeem stars.') + '</p>' +
      '<div class="field"><label for="pin-val">PIN</label>' +
      '<input class="inp mono" id="pin-val" type="password" inputmode="numeric" value="' + esc(state.pin) +
      '" style="max-width:20rem;font-size:1.5rem;letter-spacing:.4em"></div>' + pad,
      {
        title: 'Admin PIN',
        foot: '<button class="btn primary" data-act="pin-save">Unlock</button>' +
          '<button class="btn ghost" data-act="close-sheet">Cancel</button>'
      });
  }

  function pinKey(k) {
    var el = byId('pin-val');
    if (!el) return;
    if (k === 'clear') el.value = '';
    else if (k === 'del') el.value = el.value.slice(0, -1);
    else el.value += k;
  }

  /* --------------------------------------------------------- imports sheet - */

  var impSel = {};   // importId -> { index: {on, person_id} }

  function openImports() {
    var list = (D.imports && D.imports.imports) || [];
    if (!list.length) {
      openSheet('<div class="empty">Nothing waiting — forward an email to the panel address and it will appear here.</div>',
        { title: 'Magic import', focus: false });
      return;
    }
    list.forEach(function (imp) {
      if (!impSel[imp.id]) {
        impSel[imp.id] = {};
        (imp.events || []).forEach(function (ev) {
          impSel[imp.id][ev.index] = { on: true, person_id: ev.person_hint || defaultPersonId() };
        });
      }
    });
    openSheet(list.map(importBlock).join(''), {
      title: 'Magic import — ' + plural(list.length, 'email', 'emails') + ' to review',
      wide: true, focus: false
    });
  }

  function importBlock(imp) {
    var sel = impSel[imp.id] || {};
    var evs = (imp.events || []).map(function (ev) {
      var s = sel[ev.index] || { on: true, person_id: defaultPersonId() };
      var when = fmtDMY(ev.date) + (ev.all_day ? ' · all day'
        : (ev.start_time ? ' · ' + fmtTime(ev.start_time) + (ev.end_time ? ' – ' + fmtTime(ev.end_time) : '') : ''));
      return '<div class="imp-ev' + (s.on ? '' : ' is-off') + '">' +
        '<button class="box" style="width:var(--tap);height:var(--tap);border-radius:var(--radius-s)' +
        (s.on ? ';background:var(--ok);border-color:var(--ok);color:var(--on-accent)' : '') +
        '" data-act="imp-toggle" data-imp="' + esc(imp.id) + '" data-index="' + esc(ev.index) +
        '" aria-label="Include">' + ICON.tick + '</button>' +
        '<div><div class="n">' + esc(ev.title) + '</div><div class="when mono">' + esc(when) +
        (ev.location ? ' · ' + esc(ev.location) : '') + '</div></div>' +
        '<select class="inp" data-act="imp-person" data-imp="' + esc(imp.id) + '" data-index="' + esc(ev.index) + '">' +
        people().map(function (p) {
          return '<option value="' + esc(p.id) + '"' + (p.id === s.person_id ? ' selected' : '') + '>' + esc(p.name) + '</option>';
        }).join('') + '</select></div>';
    }).join('');

    return '<div class="imp"><div class="imp-head"><span class="subj">' + esc(imp.subject) + '</span>' +
      '<span class="from mono">' + esc(imp.from_addr) + '</span>' +
      '<span class="from mono">' + esc(imp.received_at || '') + '</span></div>' +
      '<div class="imp-excerpt">' + esc(imp.body_excerpt || '') + '</div>' +
      (evs || '<div class="empty">No events found in this email.</div>') +
      '<div class="imp-acts"><button class="btn primary" data-act="imp-approve" data-imp="' + esc(imp.id) + '">Approve selected</button>' +
      '<button class="btn" data-act="imp-dismiss" data-imp="' + esc(imp.id) + '">Dismiss</button></div></div>';
  }

  function approveImport(id) {
    var imp = ((D.imports && D.imports.imports) || []).filter(function (i) { return String(i.id) === String(id); })[0];
    if (!imp) return;
    var sel = impSel[id] || {};
    var chosen = (imp.events || []).filter(function (ev) { return sel[ev.index] && sel[ev.index].on; })
      .map(function (ev) { return { index: ev.index, person_id: sel[ev.index].person_id }; });
    if (!chosen.length) { toast('Tick at least one event, or dismiss the email.'); return; }
    act(POST('/api/imports/' + id + '/approve', { events: chosen }), function (r) {
      var n = (r && r.created && r.created.length) || chosen.length;
      toast(plural(n, 'event', 'events') + ' added to the calendar.');
      delete impSel[id];
      loadImports().then(function () {
        loadAgenda().then(renderSoon);
        if ((D.imports && D.imports.imports || []).length) openImports(); else closeSheet();
      });
    });
  }

  function dismissImport(id) {
    act(POST('/api/imports/' + id + '/dismiss'), function () {
      toast('Email dismissed.');
      delete impSel[id];
      loadImports().then(function () {
        render();
        if ((D.imports && D.imports.imports || []).length) openImports(); else closeSheet();
      });
    });
  }

  /* ------------------------------------------------------------ more ------ */
  /* Portrait only. Five tabs fit across the bottom of a phone; the rest of
     the panel lives behind this one, as full-width rows you can hit with a
     thumb. Nothing here is new — it is the rail's leftovers. */

  function moreRow(act, label, sub, extra) {
    return '<button class="more-row" data-act="' + act + '"' + (extra || '') + '>' +
      '<span class="more-n">' + esc(label) + '</span>' +
      (sub ? '<span class="more-s">' + sub + '</span>' : '') + '</button>';
  }

  function openMoreSheet() {
    var n = (D.imports && D.imports.imports && D.imports.imports.length) || 0;
    var body =
      '<div class="more-grid">' +
      moreRow('more-view', 'Day', 'One day at a time', ' data-view="day"') +
      moreRow('more-view', 'Month', 'The whole month', ' data-view="month"') +
      moreRow('add-event', 'Add an event', 'Straight onto the calendar') +
      moreRow('recipes', 'Recipes', 'The recipe book') +
      moreRow('imports', 'Magic import', n
        ? '<b class="more-badge">' + n + ' waiting</b>' : 'Nothing waiting') +
      (assistOn() ? moreRow('assist', 'Assist', 'Ask the house, out loud or typed') : '') +
      moreRow('settings', 'Settings', 'PIN, screen, sync status') +
      '</div>';
    openSheet(body, { title: 'More', focus: false });
  }

  /* -------------------------------------------------------- settings sheet - */

  /* ------------------------------------------------------- sync status ---- */
  /* /api/status is the panel's debug surface: which job ran when, whether it
     worked, and — the one everybody asks about — how far behind each ICS feed
     is. Refreshed in place every 30 s while the sheet is open. */

  var JOB_LABEL = {
    ics: 'Calendar sync', imap: 'Magic import mailbox',
    weather: 'Weather', ha: 'Home Assistant'
  };

  var statusTimer = null;

  function statusRow(name, o, feed) {
    var cls = !o ? 'is-idle' : (o.ok ? 'is-ok' : 'is-fail');
    var detail = !o ? 'Not run yet' : (o.detail || (o.ok ? 'ok' : 'Failed'));
    return '<div class="sync-row' + (feed ? ' is-feed' : '') + '">' +
      '<span class="sd ' + cls + '"></span>' +
      '<span class="n">' + esc(name) + '</span>' +
      '<span class="t mono">' + esc(o ? relTime(o.at) : 'never') + '</span>' +
      '<span class="d ' + cls + '">' + esc(detail) + '</span></div>';
  }

  function syncStatusHtml() {
    var s = D.status || {};
    var jobs = s.jobs || {}, feeds = s.feeds || {};
    var jk = Object.keys(jobs), fk = Object.keys(feeds);
    if (!jk.length && !fk.length) {
      return '<p class="muted2" style="font-size:.9375rem">Nothing has synced yet — the jobs run on a timer.</p>';
    }
    var out = jk.map(function (k) { return statusRow(JOB_LABEL[k] || capitalise(k), jobs[k]); }).join('');
    if (fk.length) {
      out += '<div class="sync-sub">Calendar feeds</div>' +
        fk.map(function (k) { return statusRow(k, feeds[k], true); }).join('');
    }
    return out;
  }

  function startStatusRefresh() {
    stopStatusRefresh();
    statusTimer = setInterval(function () {
      if (!byId('syncStatus')) { stopStatusRefresh(); return; }
      loadStatus().then(function () {
        var h = byId('syncStatus');
        if (h) h.innerHTML = syncStatusHtml();
      });
    }, 30000);
  }

  function stopStatusRefresh() {
    if (statusTimer) { clearInterval(statusTimer); statusTimer = null; }
  }

  /* The skin picker: five little three-band swatches — ground, card, hero —
     drawn from the same table the accent maths uses, so a swatch can never
     claim a colour the panel does not actually wear. Shown in whichever theme
     is running, because that is the panel you are looking at. */
  function skinPickerHtml() {
    return '<div class="skin-pick">' + SKINS.map(function (sk) {
      var v = sk[state.theme === 'night' ? 'night' : 'day'];
      return '<button class="skin-sw' + (sk.id === state.skin ? ' is-on' : '') +
        '" data-act="pick-skin" data-skin="' + esc(sk.id) + '"' +
        (sk.id === state.skin ? ' aria-current="true"' : '') + '>' +
        '<span class="skin-bands" style="--sw-1:' + esc(v.chrome) +
        ';--sw-2:' + esc(v.card) + ';--sw-3:' + esc(v.hero) + '">' +
        '<i></i><i></i><i></i></span>' +
        '<span class="skin-n">' + esc(sk.name) + '</span>' +
        '<span class="skin-s">' + esc(sk.note) + '</span></button>';
    }).join('') + '</div>';
  }

  function openSettings() {
    var s = D.status || {};
    var screen = (s.config && s.config.screen) || {};
    var body =
      '<div class="field"><label>Admin</label>' +
      '<p class="muted" style="margin-bottom:.625rem">' +
      (state.pin ? 'PIN entered — chore changes and star redemptions are unlocked.' :
        'Enter the PIN to change chores or redeem stars.') + '</p>' +
      '<div style="display:flex;gap:.625rem"><button class="btn" data-act="open-pin">' +
      (state.pin ? 'Change PIN' : 'Enter PIN') + '</button>' +
      (state.pin ? '<button class="btn ghost" data-act="pin-clear">Lock again</button>' : '') + '</div></div>' +

      '<div class="field" style="margin-top:1.5rem"><label>Theme</label>' +
      '<p class="muted" style="margin-bottom:.75rem">The same panel in a different ' +
      'material. Day and night keep swapping underneath whichever you pick.</p>' +
      skinPickerHtml() + '</div>' +

      '<div class="field" style="margin-top:1.5rem"><label>Screen</label>' +
      '<p class="muted">Sleeps at <span class="mono">' + esc(fmtTime(screen.sleep || '21:00')) +
      '</span>, wakes at <span class="mono">' + esc(fmtTime(screen.wake || '05:45')) +
      '</span>. Touch the sleep screen to wake it for a minute.</p></div>' +

      '<div class="field" style="margin-top:1.5rem"><label>Sync status</label>' +
      '<div class="sync-list" id="syncStatus">' + syncStatusHtml() + '</div>' +
      '<p class="muted2" style="font-size:.8125rem;margin-top:.625rem">' +
      'Updates itself every 30 seconds. A feed that lags is the feed’s own schedule, ' +
      'not the panel — the last run tells you which.</p></div>' +

      '<div class="field" style="margin-top:1.5rem"><label>About</label>' +
      '<p class="muted mono" style="font-size:.875rem">Panel v' + esc(s.version || '—') +
      ' · today ' + esc(fmtDMY(state.today)) +
      ' · last good ' + esc(fmtStamp(state.lastGoodAt)) +
      (MOCK ? ' · demo data' : '') + '</p></div>';

    openSheet(body, {
      title: 'Settings',
      foot: '<button class="btn" data-act="manage-chores">Manage chores</button>' +
        '<button class="btn" data-act="recipes">Recipes</button>' +
        '<span style="flex:1 1 auto"></span>' +
        '<button class="btn ghost" data-act="close-sheet">Close</button>',
      focus: false
    });
    startStatusRefresh();
  }

  /* --------------------------------------------------------------- assist - */
  /* Ask the house something, out loud or in writing. Everything goes through
     the backend — the browser has no Home Assistant token and never gets one.

     Two paths into the same conversation:

       text   POST /api/assist/text with {text}. Always available. This is not
              the fallback: on a wall panel in a loud kitchen it is often the
              faster one, so it sits in the sheet foot where it is always to
              hand.
       voice  getUserMedia -> AudioContext -> raw 16-bit mono PCM, posted as
              one body on release. Deliberately NOT MediaRecorder: it hands
              back a compressed stream inside a container, and the endpoint
              reads neither — it wants the bare samples.

     getUserMedia needs a secure context. A tablet hitting the panel over
     plain http has no microphone at all, and the honest thing to do there is
     say so once, quietly, next to a mic key that is visibly out of service —
     not to offer a button that throws. */

  var AS = {
    log: [],          // [{who:'you'|'panel', text, kind}] — kept for the session
    busy: false,      // a request is in flight
    rec: null,        // the live capture rig, while recording
    capTimer: null,   // the 12 s hard cap
    audio: null       // the <audio> playing the last spoken reply
  };

  var AS_CAP_MS = 12000;
  var AS_RATE = 16000;

  function assistOn() {
    var c = (D.status && D.status.config) || {};
    return !!c.assist || MOCK;
  }

  /* The demo has a microphone in the same sense that it has a Sonos. */
  function micUsable() {
    if (MOCK) return true;
    var Ctx = window.AudioContext || window.webkitAudioContext;
    /* The capture rig is built on createScriptProcessor. It is deprecated and
       a browser that has dropped it would throw halfway through opening the
       microphone, so the key says "voice is off" rather than dying mid-tap. */
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && Ctx &&
      Ctx.prototype && typeof Ctx.prototype.createScriptProcessor === 'function');
  }

  var AS_LOG_MAX = 40;

  function asSay(who, text, kind) {
    AS.log.push({ who: who, text: text, kind: kind || '' });
    /* The whole transcript is rebuilt on every paint, and a panel that is up
       for weeks would otherwise grow it without limit. */
    if (AS.log.length > AS_LOG_MAX) AS.log.splice(0, AS.log.length - AS_LOG_MAX);
    asPaint();
  }

  function asLogHtml() {
    if (!AS.log.length) {
      return '<p class="as-empty">Ask about today, or tell the house what to do — ' +
        '“what’s on today”, “turn on the porch light”.</p>';
    }
    return AS.log.map(function (m) {
      return '<div class="as-b is-' + m.who + (m.kind ? ' is-' + m.kind : '') + '">' +
        (m.kind === 'heard' ? '<span class="as-tag">Heard</span>' : '') +
        '<p>' + esc(m.text) + '</p></div>';
    }).join('');
  }

  function asState() {
    if (AS.rec) return { cls: 'is-live', hint: 'Listening… tap to send' };
    if (AS.busy) return { cls: 'is-busy', hint: 'Thinking…' };
    if (!micUsable()) {
      return {
        cls: 'is-off',
        hint: 'Voice needs the HTTPS setup — see the guide. Typing works now.'
      };
    }
    return { cls: '', hint: 'Tap to talk' };
  }

  /* Repaint in place rather than reopening the sheet: reopening would blow
     away whatever is half-typed in the text field. */
  function asPaint() {
    var log = byId('asLog');
    if (!log) return;
    log.innerHTML = asLogHtml();
    log.scrollTop = log.scrollHeight;

    var st = asState();
    var key = byId('asKey');
    key.className = 'as-key' + (st.cls ? ' ' + st.cls : '');
    key.disabled = st.cls === 'is-off' || AS.busy;
    byId('asHint').textContent = st.hint;

    var send = byId('asSend');
    if (send) send.disabled = AS.busy;
  }

  function openAssist() {
    var body =
      '<div class="as-log" id="asLog">' + asLogHtml() + '</div>' +
      '<div class="as-key-wrap">' +
      '<button class="as-key" id="asKey" data-act="assist-mic" aria-label="Tap to talk">' +
      '<span class="as-ring"></span>' + ICON.mic + '</button>' +
      '<p class="as-hint" id="asHint"></p></div>';

    openSheet(body, {
      title: 'Assist',
      cls: 'as-scrim',
      focus: false,
      foot: '<form class="as-form" data-act="assist-send">' +
        '<input class="inp" id="asText" data-keep="asText" type="text" autocomplete="off" ' +
        'placeholder="Type a question…">' +
        '<button class="btn primary" id="asSend" type="submit">Send</button></form>' +
        '<button class="btn ghost" data-act="close-sheet">Close</button>'
    });
    asPaint();
    var box = byId('asText');
    if (box) box.focus();
  }

  /* ---- text ---- */

  function assistSend(text) {
    text = String(text || '').trim();
    if (!text || AS.busy) return;
    asSay('you', text);
    AS.busy = true;
    asPaint();
    POST('/api/assist/text', { text: text }).then(function (d) {
      AS.busy = false;
      if (d && d.ok) asSay('panel', d.response_text || 'Done.');
      else asSay('panel', (d && d.error) || 'That did not get through.', 'warn');
      asAfterReply(d);
    }, function (err) {
      AS.busy = false;
      asSay('panel', err.message || 'The panel could not reach the house.', 'warn');
      asPaint();
    });
  }

  /* A spoken reply comes back as an HA-relative path. It is handed straight
     back to our own proxy — the browser must never fetch HA directly, it has
     no token and no business knowing the address. */
  function asAfterReply(d) {
    asPaint();
    if (!d || !d.tts_path) return;
    try {
      if (AS.audio) { AS.audio.pause(); AS.audio = null; }
      AS.audio = new Audio('/api/assist/tts?path=' + encodeURIComponent(d.tts_path));
      var p = AS.audio.play();
      /* autoplay can be refused before the panel has been touched; the text
         of the reply is already on screen, so there is nothing to recover */
      if (p && p.catch) p.catch(function () { });
    } catch (e) { /* no audio element: the transcript still stands */ }
  }

  /* ---- voice ---- */

  /* Bumped every time the panel asks for the microphone, and every time it
     lets one go. The permission prompt can sit on screen for as long as it
     likes; if the sheet is shut in the meantime, the stream that eventually
     arrives belongs to nobody and has to be handed straight back. */
  var asGen = 0;

  function assistMic() {
    if (AS.rec) { asStopRec(); return; }
    if (AS.busy || !micUsable()) return;
    if (MOCK) { asMockListen(); return; }

    asGen++;
    var gen = asGen;

    navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }
    }).then(function (stream) {
      function dropStream() {
        try { stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
      }
      /* Stale grant: another tap superseded this one, or the sheet is gone. */
      if (gen !== asGen || AS.rec || !byId('asKey')) { dropStream(); return; }

      var ctx = null;
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        /* ask for 16 kHz outright; some platforms quietly ignore it, which is
           why the rate the context actually chose is what gets resampled from
           below rather than the rate we asked for */
        try { ctx = new Ctx({ sampleRate: AS_RATE }); }
        catch (e) { ctx = new Ctx(); }

        var src = ctx.createMediaStreamSource(stream);
        var node = ctx.createScriptProcessor(4096, 1, 1);
        var chunks = [], total = 0;

        node.onaudioprocess = function (ev) {
          if (!AS.rec) return;
          var d = ev.inputBuffer.getChannelData(0);
          chunks.push(new Float32Array(d));
          total += d.length;
        };

        /* a ScriptProcessor only runs while it is connected to something, and
           the only thing to connect it to is the output — so it goes through a
           silent gain, otherwise the kitchen hears itself */
        var mute = ctx.createGain();
        mute.gain.value = 0;
        src.connect(node);
        node.connect(mute);
        mute.connect(ctx.destination);

        AS.rec = {
          ctx: ctx, stream: stream, node: node, src: src, mute: mute,
          chunks: chunks, rate: ctx.sampleRate,
          count: function () { return total; }
        };
      } catch (err) {
        /* Anything at all going wrong while the rig is being built has to give
           the microphone back — a wall panel holding it open is the one
           outcome that is worse than voice not working. */
        AS.rec = null;
        try { if (ctx) ctx.close(); } catch (e) { }
        dropStream();
        asSay('panel', 'The panel could not open the microphone. Typing still works.', 'warn');
        return;
      }
      clearTimeout(AS.capTimer);
      AS.capTimer = setTimeout(asStopRec, AS_CAP_MS);
      asPaint();
    }, function (err) {
      asSay('panel', err && err.name === 'NotAllowedError'
        ? 'The panel needs permission to use the microphone.'
        : 'No microphone the panel can use. Typing still works.', 'warn');
    });
  }

  function asTeardown(r) {
    try { r.node.onaudioprocess = null; r.src.disconnect(); r.node.disconnect(); r.mute.disconnect(); } catch (e) { }
    try { r.stream.getTracks().forEach(function (t) { t.stop(); }); } catch (e) { }
    try { r.ctx.close(); } catch (e) { }
  }

  function asStopRec() {
    var r = AS.rec;
    if (!r) return;
    if (r.mock) { clearTimeout(AS.capTimer); asMockReply(); return; }
    AS.rec = null;
    clearTimeout(AS.capTimer);
    asTeardown(r);

    var pcm = asToPcm16(r.chunks, r.rate);
    if (!pcm || pcm.length < AS_RATE / 4) {        // under a quarter second
      asSay('panel', 'That was too short to hear. Hold the key while you talk.', 'warn');
      return;
    }
    AS.busy = true;
    asPaint();
    assistAudio(pcm).then(function (d) {
      AS.busy = false;
      if (d && d.ok) {
        if (d.heard) asSay('you', d.heard, 'heard');
        asSay('panel', d.response_text || 'Done.');
      } else {
        asSay('panel', (d && d.error) || 'That did not get through.', 'warn');
      }
      asAfterReply(d);
    }, function (err) {
      AS.busy = false;
      asSay('panel', err.message || 'The panel could not reach the house.', 'warn');
      asPaint();
    });
  }

  /* Float32 frames -> the one thing the endpoint accepts: raw 16-bit signed
     little-endian mono PCM, no container, at 16 kHz. If the context handed
     back a different rate we resample here rather than lying in the query
     string about what is in the body. */
  function asToPcm16(chunks, rate) {
    var n = 0, i, j;
    for (i = 0; i < chunks.length; i++) n += chunks[i].length;
    if (!n) return null;

    var flat = new Float32Array(n), at = 0;
    for (i = 0; i < chunks.length; i++) { flat.set(chunks[i], at); at += chunks[i].length; }

    var out = flat;
    if (rate && Math.abs(rate - AS_RATE) > 1) {
      var ratio = rate / AS_RATE;
      var m = Math.floor(n / ratio);
      out = new Float32Array(m);
      for (j = 0; j < m; j++) {
        var pos = j * ratio, lo = Math.floor(pos), hi = Math.min(lo + 1, n - 1);
        out[j] = flat[lo] + (flat[hi] - flat[lo]) * (pos - lo);   // linear, plenty for speech
      }
    }

    var pcm = new Int16Array(out.length);
    for (j = 0; j < out.length; j++) {
      var x = out[j];
      if (x > 1) x = 1; else if (x < -1) x = -1;
      pcm[j] = x < 0 ? x * 0x8000 : x * 0x7FFF;
    }
    return pcm;
  }

  /* The one request in the panel that is not JSON, so it does not go through
     api() — but it still has to answer to the mock layer. */
  function assistAudio(pcm) {
    if (window.__mock && window.__mock.enabled) {
      return mockRequest('POST', '/api/assist/audio?sample_rate=' + AS_RATE, { bytes: pcm.length * 2 });
    }
    var opts = {
      method: 'POST', cache: 'no-store',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: pcm.buffer
    };
    if (state.pin) opts.headers['X-Admin-Pin'] = state.pin;
    /* Speech-to-text plus a conversation turn is slow — this one gets far
       longer than the ten seconds every other request is held to. */
    var disarm = armTimeout(opts, API_AUDIO_TIMEOUT_MS);
    return fetch('/api/assist/audio?sample_rate=' + AS_RATE, opts).then(function (res) {
      disarm();
      return res.json().catch(function () { return null; }).then(function (d) {
        if (!res.ok && !(d && d.error)) {
          throw new Error('The panel could not send that (' + res.status + ').');
        }
        return d;
      });
    }, function (err) {
      disarm();
      throw timedOut(err);
    });
  }

  /* The demo has no microphone and no speech to recognise, so it acts out the
     two seconds of listening and then hands over a line it "heard". */
  function asMockListen() {
    AS.rec = { mock: true };
    asPaint();
    clearTimeout(AS.capTimer);
    AS.capTimer = setTimeout(asMockReply, 2000);
  }

  function asMockReply() {
    if (!AS.rec) return;
    AS.rec = null;
    AS.busy = true;
    asPaint();
    mockRequest('POST', '/api/assist/audio?sample_rate=' + AS_RATE, { demo: true })
      .then(function (d) {
        AS.busy = false;
        if (d.heard) asSay('you', d.heard, 'heard');
        asSay('panel', d.response_text);
        asPaint();
      });
  }

  /* A sheet closing mid-sentence must let go of the microphone — a wall panel
     holding the mic open after you walked away is exactly the wrong thing. */
  function assistRelease() {
    asGen++;                     // any getUserMedia still in the air is stale
    var r = AS.rec;
    if (!r) return;
    AS.rec = null;
    clearTimeout(AS.capTimer);
    if (!r.mock) asTeardown(r);
    if (AS.audio) { try { AS.audio.pause(); } catch (e) { } AS.audio = null; }
  }

  /* --------------------------------------------------------- chore manager - */

  var DAY_CODES = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
  var SLOTS = ['morning', 'afternoon', 'evening'];

  /* One set of fields for both jobs: blank for a new chore, filled for an
     edit. Only ever one of them is on screen, so the ids can be shared. */
  function choreFields(c) {
    c = c || {};
    var days = String(c.days || '').split(',').filter(Boolean);
    var slot = c.slot || 'morning';
    return '<div class="field"><label for="c-title">Title</label>' +
      '<input class="inp" id="c-title" type="text" placeholder="Feed the dog" value="' + esc(c.title || '') + '"></div>' +
      '<div class="field"><label>Who</label>' + personPicker(c.person_id || 'kid', 'c-person') + '</div>' +
      '<div class="field"><label>Days</label><div class="pick">' +
      DAY_CODES.map(function (d) {
        return '<button type="button" class="pick-btn sm' + (days.indexOf(d) !== -1 ? ' is-on' : '') +
          '" data-act="toggle-day" data-day="' + d + '">' + d + '</button>';
      }).join('') + '<input type="hidden" id="c-days" value="' + esc(days.join(',')) + '"></div></div>' +
      '<div class="field-row"><div class="field"><label for="c-slot">Slot</label>' +
      '<select class="inp" id="c-slot">' + SLOTS.map(function (s) {
        return '<option value="' + s + '"' + (s === slot ? ' selected' : '') + '>' + capitalise(s) + '</option>';
      }).join('') + '</select></div>' +
      '<div class="field"><label for="c-stars">Stars</label>' +
      '<input class="inp mono" id="c-stars" type="number" min="1" max="5" value="' + (c.stars || 1) + '"></div></div>';
  }

  function readChoreForm() {
    var title = readForm('c-title').trim();
    var days = readForm('c-days');
    if (!title) { toast('Give the chore a name first.'); return null; }
    if (!days) { toast('Pick at least one day.'); return null; }
    return {
      title: title, person_id: readForm('c-person') || 'kid', days: days,
      slot: readForm('c-slot') || 'morning', stars: Number(readForm('c-stars')) || 1
    };
  }

  function openChoreManager() {
    if (!state.pin) { openPin('Enter the admin PIN to manage chores.'); return; }
    act(GET('/api/chores'), function (d) {
      var list = (d && d.chores) || [];
      D.choreDefs = list;
      var body = (list.length ? '<p class="muted" style="margin-bottom:.75rem">Tap a chore to change it.</p>' +
        list.map(function (c) {
          return '<div class="item chore-def" data-act="chore-edit" data-id="' + esc(c.id) +
            '" role="button" tabindex="0" style="gap:.875rem;' + pstyle(c.person_id) + '">' +
            '<span class="cd-dot"></span>' +
            '<span class="item-text" style="flex:1 1 auto">' + esc(c.title) + '</span>' +
            '<span class="mono muted2" style="font-size:.8125rem">' + esc(c.days) + ' · ' + esc(c.slot) + ' · ' + (c.stars || 1) + '★</span>' +
            '<span class="cd-edit">Edit</span>' +
            '<button class="btn sm danger" data-act="chore-delete" data-id="' + esc(c.id) + '">Delete</button></div>';
        }).join('') : '<div class="empty">No chores set up yet — add the first one below.</div>') +

        '<h3 class="lbl" style="margin:1.5rem 0 .75rem">Add a chore</h3>' + choreFields(null);

      openSheet(body, {
        title: 'Manage chores',
        foot: '<button class="btn primary" data-act="chore-add">Add chore</button>' +
          '<span style="flex:1 1 auto"></span><button class="btn ghost" data-act="close-sheet">Close</button>',
        focus: false
      });
    });
  }

  function openChoreForm(id) {
    var c = (D.choreDefs || []).filter(function (x) { return String(x.id) === String(id); })[0];
    if (!c) { toast('That chore is no longer here.'); return; }
    openSheet('<p class="muted" style="margin-bottom:1.25rem">Changes take effect today. ' +
      'Stars already earned stay where they are.</p>' + choreFields(c), {
      title: 'Edit chore',
      foot: '<button class="btn primary" data-act="chore-save" data-id="' + esc(c.id) + '">Save</button>' +
        '<button class="btn ghost" data-act="manage-chores">Cancel</button>' +
        '<span style="flex:1 1 auto"></span>' +
        '<button class="btn danger" data-act="chore-delete" data-id="' + esc(c.id) + '">Delete</button>',
      focus: false
    });
  }

  function addChore() {
    var body = readChoreForm();
    if (!body) return;
    act(POST('/api/chores', body), function () {
      toast('Chore added.');
      loadChores().then(renderSoon);
      openChoreManager();
    });
  }

  function saveChore(id) {
    var body = readChoreForm();
    if (!body) return;
    act(PATCH('/api/chores/' + id, body), function () {
      toast('Chore updated.');
      loadChores().then(renderSoon);
      openChoreManager();
    });
  }

  function deleteChore(id) {
    confirmDialog('Delete this chore?', 'It disappears from everyone\'s list from today.', 'Delete').then(function (ok) {
      if (!ok) return;
      act(DEL('/api/chores/' + id), function () {
        toast('Chore deleted.');
        loadChores().then(renderSoon);
        openChoreManager();
      });
    });
  }

  /* ------------------------------------------------------------- redeem ---- */

  function openRedeem(personId) {
    if (!state.pin) { openPin('Enter the admin PIN to redeem stars.'); return; }
    act(GET('/api/stars'), function (d) {
      D.stars = d;
      var row = ((d && d.people) || []).filter(function (p) { return p.id === personId; })[0] || { balance: 0 };
      openSheet('<p class="muted" style="margin-bottom:1rem">' + esc(nameOf(personId)) +
        ' has <b class="mono">' + (row.balance || 0) + '</b> stars saved up.</p>' +
        '<div class="field"><label for="r-stars">Stars to redeem</label>' +
        '<input class="inp mono" id="r-stars" type="number" min="1" max="' + (row.balance || 0) +
        '" value="' + Math.min(5, row.balance || 1) + '" style="max-width:10rem;font-size:1.5rem"></div>' +
        '<div class="field"><label for="r-note">What for</label>' +
        '<input class="inp" id="r-note" type="text" placeholder="Ice cream at the shops"></div>',
        {
          title: 'Redeem stars',
          foot: '<button class="btn primary" data-act="redeem-save" data-person="' + esc(personId) + '">Redeem</button>' +
            '<button class="btn ghost" data-act="close-sheet">Cancel</button>'
        });
    });
  }

  function saveRedeem(personId) {
    var n = Number(readForm('r-stars'));
    if (!n || n < 1) { toast('Enter how many stars to redeem.'); return; }
    act(POST('/api/stars/redeem', { person_id: personId, stars: n, note: readForm('r-note').trim() || null }),
      function (r) {
        closeSheet();
        toast(plural(n, 'star', 'stars') + ' redeemed — ' + ((r && r.balance) || 0) + ' left.');
        loadChores().then(renderSoon);
      });
  }

  /* --------------------------------------------------------- meal assigning */

  function openMealPicker(date, slot) {
    var current = mealFor(date, slot) || {};
    var recipes = (D.recipes && D.recipes.recipes) || [];
    var body = '<p class="muted" style="margin-bottom:1rem">' + esc(fmtLongDate(date)) + ' · ' +
      esc(fmtDMY(date)) + ' · ' + esc(slot) + '</p>' +
      '<div class="field"><label>Pick a recipe</label><div class="pick">' +
      (recipes.length ? recipes.map(function (r) {
        var t = recipeMinutes(r);
        return '<button type="button" class="pick-btn' + (current.recipe_id === r.id ? ' is-on' : '') +
          '" data-act="pick-recipe" data-value="' + esc(r.id) + '">' + esc(r.name) +
          (t ? '<span class="mchip warm">' + t + ' min</span>' : '') + '</button>';
      }).join('') : '<span class="muted2">No recipes saved yet.</span>') +
      '<input type="hidden" id="m-recipe" value="' + esc(current.recipe_id || '') + '"></div></div>' +
      '<div class="field"><label for="m-free">Or just type it</label>' +
      '<input class="inp" id="m-free" type="text" value="' + esc(current.free_text || '') +
      '" placeholder="Leftovers"></div>';

    openSheet(body, {
      title: 'Plan ' + slot,
      foot: '<button class="btn primary" data-act="meal-save" data-date="' + esc(date) + '" data-slot="' + esc(slot) + '">Save</button>' +
        (current.recipe_id ? '<button class="btn" data-act="push-groceries" data-date="' + esc(date) +
          '" data-slot="' + esc(slot) + '">Add ingredients to groceries</button>' : '') +
        '<span style="flex:1 1 auto"></span>' +
        '<button class="btn danger" data-act="meal-clear" data-date="' + esc(date) + '" data-slot="' + esc(slot) + '">Clear</button>' +
        '<button class="btn ghost" data-act="close-sheet">Cancel</button>',
      focus: false
    });
  }

  function saveMeal(date, slot, clear) {
    var rid = clear ? null : (readForm('m-recipe') ? Number(readForm('m-recipe')) : null);
    var free = clear ? null : (readForm('m-free').trim() || null);
    act(PUT('/api/mealplan', { date: date, slot: slot, recipe_id: rid, free_text: rid ? null : free }), function () {
      closeSheet();
      toast(clear ? 'Cleared.' : 'Meal saved.');
      loadMealplan(mealAnchor()).then(renderSoon);
    });
  }

  function pushGroceries(date, slot) {
    act(POST('/api/mealplan/push_groceries', { date: date, slot: slot }), function (r) {
      var added = (r && r.added) || [], skipped = (r && r.skipped) || [];
      var msg;
      if (!added.length && !skipped.length) msg = 'That meal has no ingredients saved.';
      else if (!added.length) msg = 'Nothing new — all ' + skipped.length + ' already on the list.';
      else msg = added.length + ' added' + (skipped.length ? ', ' + skipped.length + ' already on the list' : '') + '.';
      toast(msg);
      loadLists();
    });
  }

  /* ------------------------------------------------------- recipe library - */

  function recipeCard(r) {
    var host = recipeHost(r);
    return '<button class="rp-card' + (host ? ' is-imported' : '') +
      '" data-act="recipe-open" data-id="' + esc(r.id) + '">' +
      recipeMedia(r) +
      (host ? '<span class="rp-badge">Imported</span>' : '') +
      '<span class="rp-body">' +
      '<span class="rp-name">' + esc(r.name) + '</span>' +
      (r.notes ? '<span class="rp-notes">' + esc(r.notes) + '</span>' : '') +
      '<span class="rp-chips">' + recipeChips(r) + '</span></span></button>';
  }

  function openRecipes() {
    var recipes = ((D.recipes && D.recipes.recipes) || []).slice();
    var imported = recipes.filter(function (r) { return !!r.source_url; }).length;
    var body =
      '<div class="rp-bar">' +
      '<button class="btn accent" data-act="recipe-import-open">Import from the web</button>' +
      '<button class="btn" data-act="recipe-new">Add by hand</button>' +
      '<span style="flex:1 1 auto"></span>' +
      '<span class="muted2" style="font-size:.8125rem">' +
      plural(recipes.length, 'recipe', 'recipes') +
      (imported ? ' · ' + imported + ' imported' : '') + '</span></div>' +
      (recipes.length
        ? '<div class="rp-grid">' + recipes.map(recipeCard).join('') + '</div>'
        : '<div class="empty">No recipes yet — import one from the web or add it by hand.</div>');
    openSheet(body, { title: 'Recipes', wide: true, focus: false });
  }

  function openRecipeDetail(id) {
    var r = recipeById(id);
    if (!r) { toast('That recipe is no longer here.'); return; }
    var host = recipeHost(r);
    var m = r.meta || {};
    var ing = (r.ingredients || []);
    /* API.md: meta.steps is the method — three brisk steps on the starters,
       the site's own on an import. The kiosk never navigates away, so the
       method has to be readable here or it may as well not exist. */
    var steps = (m.steps || []).filter(function (s) { return String(s || '').trim(); });
    /* Photo first, ingredients second — the owner's ask is "click a recipe,
       see what's in it and what it looks like", so nothing else goes above. */
    var body =
      recipeMedia(r, 'rp-hero') +
      '<h3 class="rp-h">' + esc(r.name) + '</h3>' +
      '<div class="rp-chips" style="margin:.75rem 0 0">' + recipeChips(r, { quiet: true }) +
      (m.author ? '<span class="mchip src">' + esc(m.author) + '</span>' : '') + '</div>' +
      '<h4 class="lbl" style="margin:1.5rem 0 .5rem">Ingredients</h4>' +
      (ing.length
        ? '<ul class="ing-list">' + ing.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>'
        : '<div class="empty" style="padding:.5rem 0">No ingredients saved.</div>') +
      (steps.length
        ? '<h4 class="lbl" style="margin:1.5rem 0 .5rem">Method</h4>' +
          '<ol class="step-list">' + steps.map(function (s, i) {
            return '<li><span class="sn mono">' + (i + 1) + '</span>' +
              '<span class="st">' + esc(s) + '</span></li>';
          }).join('') + '</ol>'
        : '') +
      (r.notes ? '<p class="muted rp-note">' + esc(r.notes) + '</p>' : '') +
      /* Credit, not an instruction: the method above is the site's work, so
         the panel names the site and the author and prints the address. It is
         a kiosk locked to this app, so the URL is shown, never linked. */
      (host
        ? '<div class="rp-source-note"><div class="k">From ' + esc(host) +
          (m.author ? ' by ' + esc(m.author) : '') + ' — full original at:</div>' +
          '<div class="v">' + esc(r.source_url) + '</div></div>'
        : '');
    openSheet(body, {
      title: 'Recipe',
      wide: true,
      focus: false,
      foot: '<button class="btn" data-act="recipes">All recipes</button>' +
        '<button class="btn primary" data-act="recipe-edit" data-id="' + esc(r.id) + '">Edit</button>' +
        '<span style="flex:1 1 auto"></span>' +
        '<button class="btn danger" data-act="recipe-delete" data-id="' + esc(r.id) + '">Delete</button>' +
        '<button class="btn ghost" data-act="close-sheet">Close</button>'
    });
  }

  function openRecipeImport() {
    openSheet(
      '<div class="field"><label for="ri-url">Recipe link</label>' +
      '<input class="inp rp-url" id="ri-url" type="url" inputmode="url" autocomplete="off" ' +
      'placeholder="https://www.recipetineats.com/…">' +
      '<div class="hint">Works with RecipeTin Eats and most recipe sites.</div></div>' +
      '<p class="muted2" style="font-size:.875rem">The panel keeps the name, ingredients, times ' +
      'and the method, and credits the site it came from.</p>' +
      '<div id="ri-err"></div>',
      {
        title: 'Import a recipe',
        foot: '<button class="btn accent" data-act="recipe-import-run">Import</button>' +
          '<span style="flex:1 1 auto"></span>' +
          '<button class="btn ghost" data-act="recipes">Cancel</button>'
      });
  }

  function importError(msg) {
    var host = byId('ri-err');
    if (!host) { toast(msg); return; }
    host.innerHTML = '<div class="err-note">' + esc(msg) + '</div>';
  }

  /* Deliberately not routed through act(): a 422 from the importer is already
     a human-readable sentence and belongs in the sheet, not a toast. */
  function runRecipeImport() {
    var url = readForm('ri-url').trim();
    if (!url) { importError('Paste a recipe link first.'); return; }
    var btn = document.querySelector('[data-act="recipe-import-run"]');
    if (btn) { btn.textContent = 'Importing…'; btn.setAttribute('disabled', ''); }
    var host = byId('ri-err');
    if (host) host.innerHTML = '';
    POST('/api/recipes/import', { url: url }).then(function (r) {
      markOnline();
      renderSyncNote();
      loadRecipes().then(function () {
        render();
        openImportedRecipe(r);
      });
    }, function (err) {
      markOffline(err);
      renderSyncNote();
      if (btn) { btn.textContent = 'Import'; btn.removeAttribute('disabled'); }
      importError((err && err.message) || 'Could not reach the panel. It will retry.');
    });
  }

  function openImportedRecipe(r) {
    if (!r) { openRecipes(); return; }
    openSheet(
      '<p class="muted" style="margin-bottom:1rem">Imported and saved to the recipe book.</p>' +
      '<div class="rp-grid" style="grid-template-columns:minmax(0,22rem)">' + recipeCard(r) + '</div>',
      {
        title: 'Imported',
        focus: false,
        foot: '<button class="btn primary" data-act="recipes">Done</button>' +
          '<button class="btn" data-act="recipe-import-open">Import another</button>'
      });
  }

  /* Hand-entered and imported recipes share this form. PATCH only carries
     name/notes/ingredients, so an imported recipe keeps its source link,
     servings and times however it is edited.

     No method field on purpose: API.md's PATCH accepts name/notes/ingredients
     only — `meta.steps` is written by the importer and the starter pack, and
     a textarea here would silently throw the edit away. If the backend ever
     accepts steps, add it here and to readRecipeForm() together. */
  function openRecipeForm(r) {
    var host = r ? recipeHost(r) : null;
    openSheet(
      '<div class="field"><label for="rp-name">Name</label>' +
      '<input class="inp" id="rp-name" type="text" placeholder="Spag bol" value="' +
      esc(r ? r.name : '') + '"></div>' +
      '<div class="field"><label for="rp-ing">Ingredients (one per line)</label>' +
      '<textarea class="inp" id="rp-ing" style="min-height:11rem" ' +
      'placeholder="Beef mince&#10;Tinned tomatoes">' +
      esc(r ? (r.ingredients || []).join('\n') : '') + '</textarea></div>' +
      '<div class="field"><label for="rp-notes">Notes</label>' +
      '<input class="inp" id="rp-notes" type="text" placeholder="Optional" value="' +
      esc(r && r.notes ? r.notes : '') + '"></div>' +
      (host ? '<p class="muted2" style="font-size:.875rem">The link to ' + esc(host) +
        ', the method, the times and the servings stay as they were imported.</p>' : ''),
      {
        title: r ? 'Edit recipe' : 'Add a recipe',
        foot: (r
          ? '<button class="btn primary" data-act="recipe-save" data-id="' + esc(r.id) + '">Save changes</button>' +
            '<span style="flex:1 1 auto"></span>' +
            '<button class="btn ghost" data-act="recipe-open" data-id="' + esc(r.id) + '">Cancel</button>'
          : '<button class="btn primary" data-act="recipe-add">Add recipe</button>' +
            '<span style="flex:1 1 auto"></span>' +
            '<button class="btn ghost" data-act="recipes">Cancel</button>')
      });
  }

  function readRecipeForm() {
    var name = readForm('rp-name').trim();
    if (!name) { toast('Give the recipe a name first.'); return null; }
    return {
      name: name,
      ingredients: readForm('rp-ing').split('\n').map(function (s) { return s.trim(); })
        .filter(function (s) { return s; }),
      notes: readForm('rp-notes').trim() || null
    };
  }

  function addRecipe() {
    var body = readRecipeForm();
    if (!body) return;
    act(POST('/api/recipes', body),
      function () { toast('Recipe added.'); loadRecipes().then(openRecipes); });
  }

  function saveRecipe(id) {
    var body = readRecipeForm();
    if (!body) return;
    act(PATCH('/api/recipes/' + id, body), function () {
      toast('Recipe updated.');
      loadRecipes().then(function () { render(); openRecipeDetail(id); });
    });
  }

  /* ------------------------------------------------------------- camera ---- */

  /* One channel, full size. Opened from the wall it gets a Back key that
     returns to the wall; opened from a single-camera house it just closes. */
  function openCamera(entity, fromGrid) {
    var tiles = (D.ha && D.ha.tiles) || [];
    var t = tiles.filter(function (x) { return x.entity === entity; })[0];
    if (!t) return;
    openSheet('<div style="text-align:center">' +
      '<div class="cam-full-lab">' + esc(t.label) + '</div>' +
      '<img class="cam-full" data-cam="' + esc(entity) + '" decoding="async" src="' +
      esc(camSrc(t)) + '" alt="' + esc(t.label) + '">' +
      '<div class="cam-full-acts">' +
      (fromGrid ? '<button class="btn" data-act="cam-grid">' + ICON.left + 'All cameras</button>' : '') +
      '<button class="btn" data-act="close-sheet">Close</button></div></div>',
      { raw: true, centre: true });
  }

  /* --------------------------------------------------------------- chores -- */

  function toggleChore(el, choreId, personId) {
    var ch = D.chores;
    var p = null, c = null;
    if (ch && ch.people) {
      ch.people.forEach(function (pp) {
        (pp.chores || []).forEach(function (cc) {
          if (String(cc.id) === String(choreId)) { p = pp; c = cc; }
        });
      });
    }
    if (!c) return;
    var nowDone = !c.done;
    c.done = nowDone;
    p.stars_week = Math.max(0, (p.stars_week || 0) + (nowDone ? (c.stars || 1) : -(c.stars || 1)));
    el.classList.toggle('is-done', nowDone);

    if (nowDone && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      var burst = document.createElement('span');
      burst.className = 'burst';
      var html = '';
      for (var i = 0; i < 8; i++) html += '<i style="--r:' + (i * 45) + 'deg;animation-delay:' + (i * 12) + 'ms"></i>';
      burst.innerHTML = html;
      el.appendChild(burst);
      setTimeout(function () { if (burst.parentNode) burst.parentNode.removeChild(burst); }, 800);
    }

    act(POST('/api/chores/' + choreId + '/toggle?date=' + state.today), function () {
      setTimeout(function () { loadChores().then(renderSoon); }, 900);
    });
  }

  /* --------------------------------------------------------------- lists --- */

  function addItem(listId, input) {
    var text = input.value.trim();
    if (!text) return;
    input.value = '';
    act(POST('/api/lists/' + listId + '/items', { text: text }), function () {
      loadLists().then(renderSoon);
    });
  }

  function toggleItem(id) {
    var lists = (D.lists && D.lists.lists) || [];
    var it = null;
    lists.forEach(function (l) {
      (l.items || []).forEach(function (i) { if (String(i.id) === String(id)) it = i; });
    });
    if (!it) return;
    it.done = !it.done;
    render();
    act(PATCH('/api/items/' + id, { done: !!it.done }), function () { loadLists(); });
  }

  function deleteItem(id, text) {
    confirmDialog('Delete “' + text + '”?', 'It will be removed from the list.', 'Delete').then(function (ok) {
      if (!ok) return;
      act(DEL('/api/items/' + id), function () { toast('Deleted.'); loadLists().then(renderSoon); });
    });
  }

  /* Only lists the family added themselves offer this — the affordance is not
     rendered on Groceries or To-do at all. */
  function deleteList(id, name) {
    confirmDialog('Delete “' + name + '”?', 'The list and everything on it goes with it.', 'Delete')
      .then(function (ok) {
        if (!ok) return;
        act(DEL('/api/lists/' + id), function () { toast('List deleted.'); loadLists().then(renderSoon); });
      });
  }

  function newList() {
    openSheet('<div class="field"><label for="l-name">List name</label>' +
      '<input class="inp" id="l-name" type="text" placeholder="Hardware"></div>' +
      '<div class="field"><label for="l-kind">Kind</label>' +
      '<select class="inp" id="l-kind"><option value="custom">Custom</option>' +
      '<option value="grocery">Grocery</option><option value="todo">To-do</option></select></div>',
      {
        title: 'New list',
        foot: '<button class="btn primary" data-act="list-create">Create</button>' +
          '<button class="btn ghost" data-act="close-sheet">Cancel</button>'
      });
  }

  /* ------------------------------------------------------------ HA actions - */

  var armTimer = null;

  function haSend(btn, quiet) {
    var body = { entity: btn.dataset.entity, action: btn.dataset.action };
    if (btn.dataset.value !== undefined && btn.dataset.value !== '') {
      /* select_source carries a favourite's name — the only string value in
         the contract, so it must not go through Number(). */
      body.value = btn.dataset.vstr === '1' ? btn.dataset.value : Number(btn.dataset.value);
    }
    act(POST('/api/ha/action', body), function () {
      if (!quiet) toast('Sent.');
      setTimeout(function () { loadHa().then(renderSoon); }, 700);
    });
  }

  /* Actions whose value is just a number on the tile: tapping one paints the
     new value straight away so repeat taps step instead of fighting the poll.
     set_temperature joined them when the confirm-tap came off the aircon. */
  var STEP_ATTR = { volume_set: 'volume', set_percentage: 'percentage', set_temperature: 'setpoint' };

  function haTap(btn) {
    if (btn.dataset.now === '1') {          // media/fan: no confirm, act at once
      var action = btn.dataset.action;
      var attr = STEP_ATTR[action];
      var tiles = (D.ha && D.ha.tiles) || [];
      var t = tiles.filter(function (x) { return x.entity === btn.dataset.entity; })[0];
      if (attr) {                           // optimistic, so repeat taps step
        if (t && t.attrs) {
          t.attrs[attr] = Number(btn.dataset.value);
          if (attr === 'percentage' && t.attrs.percentage > 0) { t.attrs.on = true; t.state = 'on'; }
          render();
        }
      } else if (action === 'select_source') {
        state.npStart = { entity: btn.dataset.entity, name: btn.dataset.value, at: Date.now() };
        updateNowPlaying();
      } else if (action === 'oscillate_on' || action === 'oscillate_off') {
        if (t && t.attrs) { t.attrs.oscillating = action === 'oscillate_on'; render(); }
      }
      haSend(btn, !!attr || action === 'select_source');
      return;
    }
    if (btn.dataset.armed === '1') {
      clearTimeout(armTimer);
      btn.dataset.armed = '';
      btn.textContent = btn.dataset.label;
      haSend(btn);
      renderCatchUp();          // polls held off while the confirm stood
      return;
    }
    Array.prototype.forEach.call(document.querySelectorAll('.t-btn[data-armed="1"]'), function (b) {
      b.dataset.armed = ''; b.textContent = b.dataset.label;
    });
    btn.dataset.armed = '1';
    btn.textContent = 'Tap again to confirm';
    clearTimeout(armTimer);
    armTimer = setTimeout(function () {
      btn.dataset.armed = '';
      btn.textContent = btn.dataset.label;
      renderCatchUp();          // the confirm timed out — let the view catch up
    }, 3000);
  }

  /* ------------------------------------------------------- event delegation */

  function switchView(view) {
    state.view = view;
    state.wgScrolled = false;      // a fresh week centres itself on now again
    if (state.view === 'lists') loadLists().then(renderSoon);
    if (state.view === 'chores') loadChores().then(renderSoon);
    loadMealplan(mealAnchor()).then(renderSoon);
    loadAgenda().then(renderSoon);
    render();
    /* In portrait the stage is the page scroller, so a new view has to start
       at the top — in landscape nothing scrolls and this is a no-op. */
    if (window.scrollY) window.scrollTo(0, 0);
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest ? e.target.closest('[data-act]') : null;
    if (!el) return;
    var a = el.dataset.act;

    switch (a) {
      case 'more': openMoreSheet(); break;
      case 'more-view': closeSheet(); switchView(el.dataset.view); break;
      case 'view': switchView(el.dataset.view); break;

      case 'nav': {
        var delta = Number(el.dataset.delta);
        state.wgScrolled = false;
        if (state.view === 'day') state.cursor = addDays(state.cursor, delta);
        else if (state.view === 'week' || state.view === 'meals') state.cursor = addDays(mondayOf(state.cursor), delta * 7);
        else if (state.view === 'month') {
          var p = state.cursor.split('-').map(Number);
          var m = p[1] - 1 + delta, y = p[0] + Math.floor(m / 12);
          m = ((m % 12) + 12) % 12;
          state.cursor = y + '-' + pad2(m + 1) + '-01';
        }
        loadMealplan(mealAnchor()).then(renderSoon);
        loadAgenda().then(renderSoon);
        render();
        break;
      }

      case 'nav-today':
        state.cursor = state.today;
        state.wgScrolled = false;
        loadMealplan(mealAnchor()).then(renderSoon);
        loadAgenda().then(renderSoon);
        render();
        break;

      case 'open-day':
        state.cursor = el.dataset.date;
        state.view = 'day';
        closeSheet();
        render();
        break;

      case 'peek-day': openDayPeek(el.dataset.date, el); break;

      case 'event': openEventDetail(el.dataset.id); break;
      case 'add-event': openEventForm(null, el.dataset.date || state.cursor || state.today); break;
      case 'edit-event': {
        var ev = findEvent(el.dataset.id);
        if (ev) openEventForm(ev);
        break;
      }
      case 'save-event': saveEvent(el.dataset.id); break;
      case 'delete-event': deleteEvent(el.dataset.id); break;

      case 'chore': toggleChore(el, el.dataset.id, el.dataset.person); break;
      case 'redeem': openRedeem(el.dataset.person); break;
      case 'redeem-save': saveRedeem(el.dataset.person); break;
      case 'manage-chores': openChoreManager(); break;
      case 'chore-add': addChore(); break;
      case 'chore-edit': openChoreForm(el.dataset.id); break;
      case 'chore-save': saveChore(el.dataset.id); break;
      case 'chore-delete': deleteChore(el.dataset.id); break;

      case 'item': toggleItem(el.dataset.id); break;
      case 'add-item': break;   // handled on submit
      case 'new-list': newList(); break;
      case 'list-create': {
        var name = readForm('l-name').trim();
        if (!name) { toast('Give the list a name first.'); break; }
        act(POST('/api/lists', { name: name, kind: readForm('l-kind') || 'custom' }), function () {
          closeSheet(); loadLists().then(renderSoon);
        });
        break;
      }

      case 'meal': openMealPicker(el.dataset.date, el.dataset.slot); break;
      case 'meal-save': saveMeal(el.dataset.date, el.dataset.slot, false); break;
      case 'meal-clear': saveMeal(el.dataset.date, el.dataset.slot, true); break;
      case 'push-groceries':
        e.stopPropagation();
        pushGroceries(el.dataset.date, el.dataset.slot);
        break;
      case 'toggle-meals': state.showOtherMeals = !state.showOtherMeals; render(); break;
      case 'recipes': openRecipes(); break;
      case 'recipe-new': openRecipeForm(null); break;
      case 'recipe-open': openRecipeDetail(el.dataset.id); break;
      case 'recipe-edit': openRecipeForm(recipeById(el.dataset.id)); break;
      case 'recipe-save': saveRecipe(el.dataset.id); break;
      case 'recipe-import-open': openRecipeImport(); break;
      case 'recipe-import-run': runRecipeImport(); break;
      case 'recipe-add': addRecipe(); break;
      case 'recipe-delete': {
        var rid = el.dataset.id;
        var rr = recipeById(rid);
        confirmDialog('Delete “' + ((rr && rr.name) || 'this recipe') + '”?',
          'It will be removed from the recipe book. Meals already planned keep their name.',
          'Delete').then(function (ok) {
            if (!ok) return;
            act(DEL('/api/recipes/' + rid), function () {
              toast('Recipe deleted.');
              loadRecipes().then(function () { render(); openRecipes(); });
            });
          });
        break;
      }
      case 'pick-recipe': {
        var host = el.parentNode;
        Array.prototype.forEach.call(host.querySelectorAll('.pick-btn'), function (b) { b.classList.remove('is-on'); });
        var hid = host.querySelector('input[type=hidden]');
        if (hid.value === el.dataset.value) { hid.value = ''; }
        else { hid.value = el.dataset.value; el.classList.add('is-on'); byId('m-free').value = ''; }
        break;
      }

      case 'imports': openImports(); break;
      case 'imp-toggle': {
        var s = impSel[el.dataset.imp] && impSel[el.dataset.imp][el.dataset.index];
        if (s) {
          s.on = !s.on;
          var sb = document.querySelector('.sheet-body');
          var top = sb ? sb.scrollTop : 0;
          openImports();
          sb = document.querySelector('.sheet-body');
          if (sb) sb.scrollTop = top;
        }
        break;
      }
      case 'imp-approve': approveImport(el.dataset.imp); break;
      case 'imp-dismiss': dismissImport(el.dataset.imp); break;

      case 'settings': openSettings(); break;
      case 'assist': openAssist(); break;
      case 'assist-mic': assistMic(); break;
      case 'assist-send': break;   // handled on submit
      case 'pick-skin': {
        if (el.dataset.skin === state.skin) break;
        applySkin(el.dataset.skin);
        /* redraw the sheet in the new material, holding its scroll — the
           picker has to repaint anyway to move the ring */
        var sb = document.querySelector('.sheet-body');
        var top = sb ? sb.scrollTop : 0;
        openSettings();
        sb = document.querySelector('.sheet-body');
        if (sb) sb.scrollTop = top;
        break;
      }
      case 'open-pin': openPin(); break;
      case 'pin-key': pinKey(el.dataset.key); break;
      case 'pin-save':
        state.pin = readForm('pin-val').trim();
        closeSheet();
        toast(state.pin ? 'PIN saved for this session.' : 'PIN cleared.');
        break;
      case 'pin-clear': state.pin = ''; closeSheet(); toast('Locked again.'); break;

      case 'pick-person': {
        var wrap = el.parentNode;
        Array.prototype.forEach.call(wrap.querySelectorAll('.pick-btn'), function (b) { b.classList.remove('is-on'); });
        el.classList.add('is-on');
        byId(el.dataset.name).value = el.dataset.value;
        break;
      }

      case 'toggle-day': {
        el.classList.toggle('is-on');
        var box = el.parentNode;
        var days = Array.prototype.filter.call(box.querySelectorAll('.pick-btn'), function (b) {
          return b.classList.contains('is-on');
        }).map(function (b) { return b.dataset.day; });
        byId('c-days').value = days.join(',');
        break;
      }

      case 'toggle': {
        el.classList.toggle('is-on');
        var on = el.classList.contains('is-on');
        byId(el.dataset.name).value = on ? '1' : '';
        if (el.dataset.name === 'f-allday' && byId('f-when')) {
          byId('f-when').classList.toggle('is-allday', on);
        }
        break;
      }

      case 'ha': haTap(el); break;
      case 'cam': openCamera(el.dataset.entity, el.dataset.from === 'grid'); break;
      case 'cams': openCameraGrid(); break;
      case 'cam-grid': openCameraGrid(); break;
      case 'media-open': openNowPlaying(el.dataset.entity); break;
      case 'climate-open': openClimateSheet(); break;
      case 'solar-open': openSolarSheet(); break;
      case 'np-seek':
        if (e.detail) npSeek(el, e.clientX);   // detail 0 = keyboard, no point on the bar
        break;

      case 'close-sheet': closeSheet(); break;
      case 'scrim': if (e.target === el) closeSheet(); break;
      default: break;
    }
  });

  document.addEventListener('submit', function (e) {
    var form = e.target.closest ? e.target.closest('form[data-act]') : null;
    if (!form) return;
    if (form.dataset.act === 'add-item') {
      e.preventDefault();
      addItem(form.dataset.list, form.querySelector('input'));
    } else if (form.dataset.act === 'assist-send') {
      e.preventDefault();
      var box = byId('asText');
      if (box) { assistSend(box.value); box.value = ''; box.focus(); }
    }
  });

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (!el || !el.dataset) return;
    if (el.dataset.act === 'imp-person') {
      var sel = impSel[el.dataset.imp] && impSel[el.dataset.imp][el.dataset.index];
      if (sel) sel.person_id = el.value;
    }
  });

  document.addEventListener('input', function (e) {
    if (e.target && e.target.id === 'f-date') {
      var h = byId('f-date-hint');
      if (h) h.textContent = e.target.value ? fmtDMY(e.target.value) : '';
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeSheet();
  });

  /* The moment the caret leaves a field, whatever the polls wanted to redraw
     while it was there is allowed through. focusout fires before the new
     focus settles, so ask on the next turn of the loop. */
  document.addEventListener('focusout', function () {
    setTimeout(renderCatchUp, 0);
  }, true);

  /* long-press to delete: list items, and the family's own lists */
  var lpTimer = null, lpEl = null;

  function lpClear() {
    clearTimeout(lpTimer);
    if (lpEl) lpEl.classList.remove('is-arm');
    lpEl = null;
  }

  document.addEventListener('pointerdown', function (e) {
    /* Two fingers down at once used to orphan the first timer — it was
       reassigned, never cleared, and both long-presses fired. */
    clearTimeout(lpTimer);
    if (lpEl) return;
    var el = e.target.closest ? e.target.closest('[data-long]') : null;
    if (!el) return;
    lpEl = el;
    el.classList.add('is-arm');
    lpTimer = setTimeout(function () {
      var kind = el.dataset.long, id = el.dataset.id;
      var text = el.dataset.text, name = el.dataset.name;
      lpClear();
      if (kind === 'list') deleteList(id, name);
      else deleteItem(id, text);
    }, 600);
  }, { passive: true });
  ['pointerup', 'pointercancel', 'pointerleave', 'scroll'].forEach(function (ev) {
    document.addEventListener(ev, lpClear, true);
  });

  /* ------------------------------------------------------------ sleep mode - */

  function parseHM(s) {
    var p = String(s || '').split(':').map(Number);
    return (p[0] || 0) * 60 + (p[1] || 0);
  }

  function inSleepWindow() {
    if (FORCE_SLEEP) return true;
    var sc = (D.status && D.status.config && D.status.config.screen) || {};
    if (!sc.sleep || !sc.wake) return false;
    var s = parseHM(sc.sleep), w = parseHM(sc.wake), n = nowMins();
    return s < w ? (n >= s && n < w) : (n >= s || n < w);
  }

  function updateSleep() {
    var el = byId('sleep');
    var should = inSleepWindow() && Date.now() > state.wakeUntil;
    if (should) {
      var d = new Date();
      var parts = fmtTime(pad2(d.getHours()) + ':' + pad2(d.getMinutes())).split(' ');
      byId('sleepClock').innerHTML = esc(parts[0]) + '<i>' + esc(parts[1]) + '</i>';
      byId('sleepDate').textContent = fmtLongDate(state.today) + ' · ' + fmtDMY(state.today);
    }
    var wasUp = !el.hidden;
    if (el.hidden === should) el.hidden = !should;
    /* The wall just came back. Everything held off behind the overlay catches
       up in one go rather than waiting out each timer's next beat. */
    if (wasUp && !should) { renderSoon(); kickLoaders(); }
  }

  byId('sleep').addEventListener('pointerdown', function () {
    state.wakeUntil = Date.now() + 60000;
    updateSleep();
  });

  /* ----------------------------------------------------------- screensaver - */
  /* Leave the panel alone for a few minutes and it turns into a picture
     frame: one photograph filling the screen, the time, and what is on next.
     Nothing else — the moment it grows a tile it stops being a photograph on
     a wall and goes back to being a computer.

     Every image comes from the local cache the backend keeps (/api/photos),
     so the panel still never reaches outside for a picture.

     It gives way to two things without argument: any sheet or armed confirm
     (you were in the middle of something), and the sleep overlay (at 9 pm the
     wall goes dark, photos or not). */

  var SV = {
    on: false,
    idx: -1,
    front: 'A',                 // which layer is currently showing
    slideTimer: null,
    clockTimer: null,
    goingTimer: null,
    lastInput: Date.now()
  };

  function svCfg() {
    var d = D.photos || {};
    return {
      photos: d.photos || [],
      interval: Math.max(8, Number(d.interval_s) || 45) * 1000,
      idleMs: svIdleMs(d)
    };
  }

  /* ?idle=<seconds> shortens the wait — the only way to exercise the idle
     trigger inside a screenshot run without sitting there for three minutes.
     Without it the backend's own idle_minutes stands. */
  function svIdleMs(d) {
    var forced = Number(Q.get('idle'));
    if (forced > 0) return forced * 1000;
    return Math.max(1, Number(d.idle_minutes) || 3) * 60000;
  }

  /* Real photos come back as ids; the demo layer hands over data URIs on the
     same records, so both walk the same path from here on. */
  function svSrc(p) {
    return p.src || ('/api/photos/' + encodeURIComponent(p.id) + '/image');
  }

  function svBusy() {
    /* .innerHTML !== '' serialised an open sheet's whole subtree to a string
       thirty times a minute — asking whether it has a child is free. */
    return byId('sheetHost').firstChild !== null ||
      !!document.querySelector('[data-armed="1"]') ||
      !byId('sleep').hidden;
  }

  /* The one line of agenda the frame is allowed to carry: the next thing on
     today, or — once today is done — the first thing tomorrow. */
  function svNextUp() {
    var n = nowMins(), i;
    var today = D.byDate[state.today] || [];
    for (i = 0; i < today.length; i++) {
      var e = today[i];
      if (e.all_day || isHoliday(e)) continue;
      if (mins(e.start) > n) return { ev: e, when: 'Next up' };
    }
    var tmw = D.byDate[addDays(state.today, 1)] || [];
    for (i = 0; i < tmw.length; i++) {
      if (!isHoliday(tmw[i])) return { ev: tmw[i], when: 'Tomorrow' };
    }
    return null;
  }

  function svPaintChip() {
    var host = byId('svNext');
    var up = svNextUp();
    if (!up) { host.className = 'sv-next'; host.innerHTML = ''; return; }
    var e = up.ev;
    host.className = 'sv-next is-on';
    host.style.cssText = e.person_id ? pstyle(e.person_id) : '--c:#FFFFFF';
    host.innerHTML = '<span class="w">' + esc(up.when) + '</span>' +
      (e.person_id ? '<span class="dot"></span>' : '') +
      '<span class="t">' + esc(e.all_day ? 'All day' : fmtTime(e.start)) + '</span>' +
      '<span class="n">' + esc(e.title) + '</span>';
  }

  function svPaintClock() {
    var d = new Date();
    var parts = fmtTime(pad2(d.getHours()) + ':' + pad2(d.getMinutes())).split(' ');
    byId('svClock').innerHTML = esc(parts[0]) + '<i>' + esc(parts[1]) + '</i>';
    byId('svDate').textContent = fmtLongDate(state.today);
    svPaintChip();
  }

  /* Load the next photo off screen, and only swap once it has actually
     decoded — a half-drawn frame on a wall display looks broken. A photo the
     backend cannot serve is skipped rather than shown as a hole. */
  function svAdvance(tries) {
    var cfg = svCfg();
    if (!cfg.photos.length) { svStop(); return; }
    if (tries === undefined) tries = 0;
    if (tries >= cfg.photos.length) return;      // every one of them failed

    SV.idx = (SV.idx + 1) % cfg.photos.length;
    var src = svSrc(cfg.photos[SV.idx]);
    var probe = new Image();
    probe.onload = function () {
      if (!SV.on) return;
      var next = SV.front === 'A' ? byId('svB') : byId('svA');
      var prev = SV.front === 'A' ? byId('svA') : byId('svB');
      next.style.backgroundImage = 'url("' + src.replace(/"/g, '%22') + '")';
      /* restart the drift on the incoming layer, and let it run a touch
         longer than the slide so it never freezes before the cross-fade */
      next.style.animationDuration = ((svCfg().interval + 1400) / 1000) + 's';
      next.classList.remove('is-on');
      void next.offsetWidth;                     // reflow: replay the animation
      next.classList.add('is-on');
      prev.classList.remove('is-on');
      SV.front = SV.front === 'A' ? 'B' : 'A';
    };
    probe.onerror = function () {
      if (SV.on) svAdvance(tries + 1);
    };
    probe.src = src;
  }

  function svStart() {
    if (SV.on) return false;
    var cfg = svCfg();
    if (!cfg.photos.length) return false;
    SV.on = true;
    clearTimeout(SV.goingTimer);
    var el = byId('saver');
    el.classList.remove('is-going');
    el.hidden = false;
    byId('svA').classList.remove('is-on');
    byId('svB').classList.remove('is-on');
    SV.idx = -1;
    SV.front = 'A';
    svPaintClock();
    svAdvance();
    clearInterval(SV.slideTimer);
    SV.slideTimer = setInterval(function () { svAdvance(); }, cfg.interval);
    clearInterval(SV.clockTimer);
    SV.clockTimer = setInterval(svPaintClock, 15000);
    return true;
  }

  function svStop() {
    if (!SV.on) return;
    SV.on = false;
    /* Renders and camera refreshes were paused behind the frame — one pass
       now, so the wall is current by the time the fade finishes. */
    renderSoon();
    kickLoaders();
    clearInterval(SV.slideTimer); SV.slideTimer = null;
    clearInterval(SV.clockTimer); SV.clockTimer = null;
    SV.lastInput = Date.now();
    var el = byId('saver');
    /* fade out, but stay in front until the fade finishes: the tap that
       dismissed the frame must not also land on whatever is underneath */
    el.classList.add('is-going');
    clearTimeout(SV.goingTimer);
    SV.goingTimer = setTimeout(function () {
      el.hidden = true;
      el.classList.remove('is-going');
      byId('svA').classList.remove('is-on');
      byId('svB').classList.remove('is-on');
    }, 240);
  }

  /* The view underneath was never touched, so dismissing lands exactly where
     the panel was — a repaint is only here to catch up the clock and the
     now-rule after a few idle minutes. */
  function svDismiss(e) {
    if (!SV.on) return;
    if (e) { e.preventDefault(); e.stopPropagation(); }
    svStop();
    render();
  }

  byId('saver').addEventListener('pointerdown', svDismiss);
  byId('saver').addEventListener('click', function (e) {
    e.preventDefault(); e.stopPropagation();     // the tap's second half
  });

  document.addEventListener('pointerdown', function () {
    SV.lastInput = Date.now();
  }, { passive: true, capture: true });
  document.addEventListener('keydown', function () {
    SV.lastInput = Date.now();
    if (SV.on) svDismiss();
  }, true);

  function updateSaver() {
    if (SV.on) {
      if (svBusy()) svStop();
      return;
    }
    if (svBusy() || inSleepWindow()) return;
    if (Date.now() - SV.lastInput < svCfg().idleMs) return;
    svStart();
  }

  window.__saver = {
    start: function () { return svStart(); },
    stop: function () { svStop(); },
    running: function () { return SV.on; }
  };

  /* --------------------------------------------------------------- ticking - */

  /* Midnight. The date the panel calls "today" must not move until the new
     day's agenda is actually in hand — flipping first left a second or two of
     "Nothing on" on the wall while the fetch was still in the air, which on a
     day view (whose range is exactly one day) was a whole empty screen. */
  function rollOver(t) {
    if (state.rolling) return;
    state.rolling = true;
    var moveCursor = state.cursor === state.today;
    fetchAgenda(neededRange(t, moveCursor ? t : state.cursor)).then(function () {
      state.today = t;
      if (moveCursor) state.cursor = t;
      state.rolling = false;
      renderRail();
      loadChores().then(renderPoll);
      loadMealplan(mealAnchor());
      renderSoon();
    });
  }

  function tick() {
    var t = todayStr();
    if (t !== state.today) rollOver(t);      // midnight rollover
    renderSpine();
    renderWeekNow();
    // refresh the now rule + past-event dimming, but never mid-confirm
    if (!panelDark() && (state.view === 'today' || state.view === 'day') &&
        !armedTile()) {
      var v = byId('view');
      if (v && v.querySelector('.now-rule')) renderPoll();
    }
    updateTheme();          // dusk/dawn is checked on the ordinary minute tick
    updateSleep();
    updateSaver();
    renderSyncNote();
    renderCatchUp();        // anything a text field or a confirm held off
    watchdog();
  }

  /* Coming back from screen-off, from the sleep overlay, or from the picture
     frame: the clock and the agenda can be a minute out, so ask for
     everything at once rather than waiting out the next beat of each timer. */
  function kickLoaders() {
    loadHa().then(renderPoll);
    loadAgenda().then(renderPoll);
    loadChores().then(renderPoll);
    loadStatus().then(renderRail);
  }

  /* Android turns the kiosk's page hidden when the screen goes off — every
     timer in here is throttled or stopped while it is, so the first thing on
     waking is a full catch-up. */
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) return;
    tick();
    kickLoaders();
  });

  /* ------------------------------------------------------------------ boot - */

  function boot() {
    var v = Q.get('view');
    if (v && TABS.some(function (t) { return t.id === v; })) state.view = v;

    /* ?skin= beats the stored choice, for screenshots — it does not persist. */
    var wantSkin = Q.get('skin');
    applySkin(wantSkin && skinDef(wantSkin).id === wantSkin ? wantSkin : readSkin(),
      { animate: false, render: false, persist: false });

    var forced = Q.get('theme');
    if (forced === 'night' || forced === 'day') state.themeMode = forced;
    applyTheme(state.themeMode === 'auto' ? autoTheme() : state.themeMode, false);
    renderThemeDebug();

    render();

    updateSleep();

    // status first: people/colours and the screen schedule shape everything else
    loadStatus()
      .then(function (st) {
        /* A panel that boots while the add-on is still starting used to get
           one shot at /api/status and then wait five minutes with no people,
           no colours and no screen schedule. Keep asking, quickly at first. */
        if (!st) statusRetry(5000);
        render();
        updateSleep();
        return Promise.all([loadAgenda(), loadChores(), loadWeather(), loadHa(), loadPhotos()]);
      })
      .then(function () {
        updateTheme(false);      // real sunset/sunrise have landed by now
        render();
        return Promise.all([loadImports(), loadRecipes(), loadLists(), loadMealplan(mealAnchor())]);
      })
      .then(function () {
        state.booted = true;
        render();
        var sheet = Q.get('sheet');
        if (sheet === 'imports') openImports();
        else if (sheet === 'settings') openSettings();
        else if (sheet === 'event') {
          var first = (D.byDate[state.today] || [])[0];
          if (first) openEventDetail(first.id);
        } else if (sheet === 'add') openEventForm(null, state.today);
        else if (sheet === 'pin') openPin();
      })
      .catch(function (err) {
        console.warn('boot', err && err.message);
      });

    /* Turning the panel swaps the week between the time grid and the stacked
       cards — that choice is made in JS, so it needs a nudge to re-render. */
    if (PORTRAIT_MQ) {
      var onFlip = function () { render(); };
      if (PORTRAIT_MQ.addEventListener) PORTRAIT_MQ.addEventListener('change', onFlip);
      else if (PORTRAIT_MQ.addListener) PORTRAIT_MQ.addListener(onFlip);
    }

    /* The tiles poll. A house that is off, or a backend that has gone away,
       used to be asked again every 15 s for as long as the panel was up — so
       consecutive failures stretch the beat out to four minutes and it snaps
       straight back the moment one answer lands. */
    var haBeat = 0;
    setInterval(function () {
      haBeat++;
      var every = haFail < 2 ? 1 : Math.min(16, Math.pow(2, haFail - 1));
      if (haBeat % every !== 0) return;
      solo('poll:ha', loadHa).then(renderPoll);
    }, 15000);
    setInterval(function () {
      solo('poll:agenda', loadAgenda).then(renderPoll);
      solo('poll:chores', loadChores).then(renderPoll);
      solo('poll:imports', loadImports).then(renderRail);
    }, 60000);
    setInterval(function () {
      solo('poll:status', loadStatus).then(renderRail);
      solo('poll:weather', loadWeather).then(function () { updateTheme(); renderPoll(); });
      solo('poll:mealplan', function () { return loadMealplan(mealAnchor()); }).then(renderPoll);
      solo('poll:photos', loadPhotos);
      /* Both of these are loaded once at boot and never again, so a boot that
         raced the add-on's start left the panel with no recipes and no lists
         for the rest of the week. */
      if (!D.recipes) solo('poll:recipes', loadRecipes).then(renderPoll);
      if (!D.lists) solo('poll:lists', loadLists).then(renderPoll);
    }, 300000);
    setInterval(tick, 30000);
    /* the idle check runs on its own short beat — the 30 s tick would blur
       the moment the frame appears, and a sheet closing has to release it
       straight away */
    setInterval(updateSaver, 2000);
    camTimer = setInterval(function () {
      /* Behind the sleep overlay or the picture frame nobody can see a camera,
         and a view with no camera in it has nothing to refresh — either way
         this is 8,600 pointless fetches a day per channel on an eMMC tablet. */
      if (panelDark()) return;
      if (!document.querySelector('img[data-cam]')) return;
      stepCameraCycle();      // the grouped tile steps to the next channel…
      refreshCameras();       // …and every visible frame is re-fetched
    }, 10000);
  }

  /* /api/status is the one boot load nothing else can stand in for. Back off
     5 s, 10 s, 20 s, 30 s and then hold at 30 s until it answers. */
  function statusRetry(wait) {
    if (D.status) return;
    setTimeout(function () {
      if (D.status) return;
      loadStatus().then(function (d) {
        if (!d) { statusRetry(Math.min(30000, wait * 2)); return; }
        updateSleep();
        updateTheme(false);
        renderRail();
        renderSoon();
        kickLoaders();
      });
    }, wait);
  }

  /* =========================================================================
     MOCK LAYER — demo data + integration-test hook.
     Active only with ?mock=1 in the URL; otherwise nothing below runs.
     ====================================================================== */

  var M = null;

  /* What the demo Sonos has queued up — the overlay steps through these. */
  var TRACKS = [
    { title: 'Gold Chains', artist: 'Angus & Julia Stone' },
    { title: 'Cattle and Cane', artist: 'The Go-Betweens' },
    { title: 'Elephant', artist: 'Tame Impala' },
    { title: 'Riptide', artist: 'Vance Joy' },
    { title: 'Throw Your Arms Around Me', artist: 'Hunters & Collectors' }
  ];

  /* The demo speaker's saved favourites — the shape a real Sonos hands HA:
     a couple of radio stations and a couple of playlists, no metadata. */
  var FAVOURITES = [
    'Triple J', 'ABC Kids listen', 'ABC Classic',
    'Double J', 'Kitchen Disco', 'Sunday Morning Coffee'
  ];

  var SOURCE_NOW = {
    'Triple J': { title: 'Like A Version', artist: 'triple j' },
    'ABC Kids listen': { title: 'Play School — Songtime', artist: 'ABC Kids listen' },
    'ABC Classic': { title: 'Clarinet Concerto in A', artist: 'Mozart' },
    'Double J': { title: 'The Arvo Shift', artist: 'Double J' },
    'Kitchen Disco': { title: 'September', artist: 'Earth, Wind & Fire' },
    'Sunday Morning Coffee': { title: 'Harvest Moon', artist: 'Neil Young' }
  };

  /* --------------------------------------------------------- demo photos -- */
  /* Stand-ins for the real cached photos: warm gradient, a plate seen from
     above, food-shaped blobs. Drawn as data URIs so ?mock=1 stays completely
     self-contained — the panel never reaches outside for an image. */

  /* ---------------------------------------------------- demo screensaver -- */
  /* Six landscapes for the screensaver, drawn rather than downloaded: a sky
     gradient, an optional sun or moon, a band of water, and two or three
     ridges in front of it. Nowhere near a photograph, but at wall distance
     they carry the shape of one — and the demo stays self-contained, which a
     real JPEG could not.

     Everything is 1280x800 so the frame's object-fit has the same job it has
     with the real cache. */

  function scenePhoto(o) {
    var g = o.sky.map(function (st, i) {
      return '<stop offset="' + (i / (o.sky.length - 1)) + '" stop-color="' + st + '"/>';
    }).join('');
    var s = '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800" viewBox="0 0 1280 800">' +
      '<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1">' + g + '</linearGradient>' +
      '<radialGradient id="v" cx="0.5" cy="0.42" r="0.8">' +
      '<stop offset="0.55" stop-color="#000" stop-opacity="0"/>' +
      '<stop offset="1" stop-color="#000" stop-opacity="0.28"/></radialGradient></defs>' +
      '<rect width="1280" height="800" fill="url(#s)"/>' +
      (o.stars || '') +
      (o.orb ? '<circle cx="' + o.orb[0] + '" cy="' + o.orb[1] + '" r="' + o.orb[2] +
        '" fill="' + o.orb[3] + '" opacity="' + (o.orb[4] || 1) + '"/>' : '') +
      (o.water ? '<rect y="' + o.water[0] + '" width="1280" height="' + (800 - o.water[0]) +
        '" fill="' + o.water[1] + '"/>' +
        '<g stroke="' + (o.water[2] || '#FFFFFF') + '" stroke-opacity="0.10" stroke-width="2">' +
        '<path d="M90 ' + (o.water[0] + 16) + 'h360M620 ' + (o.water[0] + 14) +
        'h420M300 ' + (o.water[0] + 34) + 'h520M760 ' + (o.water[0] + 40) +
        'h300"/></g>' : '') +
      o.ridges.map(function (r) {
        return '<path d="' + r[0] + '" fill="' + r[1] + '"/>';
      }).join('') +
      (o.extra || '') +
      '<rect width="1280" height="800" fill="url(#v)"/></svg>';
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(s);
  }

  /* A gum treeline along a ridge: round crowns on thin trunks, heights
     wandering the way a real one does. Small enough to read as trees at wall
     distance and nothing at all up close, which is the right way round. */
  function treeline(y, colour) {
    var hs = [30, 44, 26, 52, 34, 40, 24, 58, 32, 46, 28, 50, 36, 42];
    var out = '', x = -10, i = 0;
    while (x < 1300) {
      var h = hs[i % hs.length];
      out += '<ellipse cx="' + x + '" cy="' + (y - h) + '" rx="' + (h * 0.46).toFixed(1) +
        '" ry="' + (h * 0.38).toFixed(1) + '"/>' +
        '<rect x="' + (x - 1.8).toFixed(1) + '" y="' + (y - h) + '" width="3.6" height="' + h + '"/>';
      x += h * 0.62 + 16;
      i++;
    }
    return '<g fill="' + colour + '">' + out + '</g>';
  }

  /* one ridge silhouette at height h, in three temperaments */
  function ridge(h, kind) {
    if (kind === 'flat') {
      return 'M0 800V' + h + 'H1280V800z';
    }
    if (kind === 'dune') {
      return 'M0 800V' + (h + 60) + 'c180-70 300 30 470-40s330-90 810 20V800z';
    }
    if (kind === 'range') {
      return 'M0 800V' + h + 'l190-72 150 54 220-96 210 78 210-58 300 66V800z';
    }
    return 'M0 800V' + h + 'c220-58 330 44 560-6s360-96 720-14V800z';
  }

  var DEMO_PHOTOS = [
    /* Noosa North Shore, first light */
    scenePhoto({
      sky: ['#2E3E63', '#8A6C86', '#E29A72', '#F6C892'],
      orb: [880, 470, 46, '#FFE9C0', 0.95],
      water: [520, '#4C5A78'],
      ridges: [[ridge(560, 'dune'), '#C8A379'], [ridge(690, 'dune'), '#8E7150']]
    }),
    /* gum trees against a dusk sky */
    scenePhoto({
      sky: ['#1B2440', '#3F3B63', '#8A5F6E', '#D08A63'],
      orb: [420, 560, 30, '#FFD9A0', 0.9],
      ridges: [[ridge(624, 'flat'), '#2C3A34'], [ridge(700, ''), '#131E1B']],
      extra: treeline(626, '#1B2823')
    }),
    /* headland and open sea, middle of the day */
    scenePhoto({
      sky: ['#3E7FB4', '#7FB4D6', '#BFDCE9'],
      orb: [1080, 130, 54, '#FFF6DC', 0.6],
      water: [430, '#2E6E86'],
      ridges: [[ridge(470, 'range'), '#3D6B52'], [ridge(660, ''), '#27503C']]
    }),
    /* a storm walking across the paddocks */
    scenePhoto({
      sky: ['#39414F', '#5A6470', '#93958F', '#C9B98C'],
      water: null,
      /* the rain sits behind the ranges, where rain actually is */
      stars: '<g stroke="#DCE3E8" stroke-opacity="0.13" stroke-width="2">' +
        '<path d="M240 150l-26 150M330 120l-26 170M410 180l-24 140M500 100l-26 180' +
        'M580 160l-24 150M665 130l-26 165M745 190l-24 130M830 110l-26 175' +
        'M910 170l-24 145M995 140l-26 160"/></g>',
      ridges: [[ridge(520, 'range'), '#4A5560'], [ridge(620, ''), '#7C7A5E'],
      [ridge(700, 'dune'), '#B49A5C']]
    }),
    /* the river bend, late afternoon */
    scenePhoto({
      sky: ['#7FA2A0', '#C2CDA8', '#EBDDB0'],
      orb: [340, 220, 38, '#FFF0C4', 0.7],
      water: [500, '#5C7C74'],
      ridges: [[ridge(450, ''), '#436052'], [ridge(560, ''), '#2F4A3D'],
      [ridge(720, 'dune'), '#20342B']]
    }),
    /* dunes under a clear night */
    scenePhoto({
      sky: ['#080D1E', '#132146', '#2A3A63', '#4A5677'],
      orb: [1010, 180, 34, '#F4F0DE', 0.9],
      stars: '<g fill="#FFFFFF" fill-opacity="0.75">' +
        '<circle cx="180" cy="120" r="2.4"/><circle cx="330" cy="220" r="1.8"/>' +
        '<circle cx="520" cy="90" r="2.2"/><circle cx="700" cy="240" r="1.6"/>' +
        '<circle cx="860" cy="120" r="2"/><circle cx="1160" cy="300" r="1.8"/>' +
        '<circle cx="260" cy="330" r="1.6"/><circle cx="620" cy="380" r="1.4"/></g>',
      ridges: [[ridge(600, 'dune'), '#1B2135'], [ridge(700, 'dune'), '#0D111C']]
    })
  ];

  function foodPhoto(o) {
    var s =
      '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300">' +
      '<defs><linearGradient id="g" x1="0" y1="0" x2="0.75" y2="1">' +
      '<stop offset="0" stop-color="' + o.bg1 + '"/><stop offset="1" stop-color="' + o.bg2 + '"/></linearGradient>' +
      '<radialGradient id="v" cx="0.48" cy="0.42" r="0.78">' +
      '<stop offset="0.5" stop-color="#000" stop-opacity="0"/>' +
      '<stop offset="1" stop-color="#000" stop-opacity="0.3"/></radialGradient></defs>' +
      '<rect width="400" height="300" fill="url(#g)"/>' +
      '<g opacity="0.1" stroke="#2A1B10" stroke-width="3">' +
      '<path d="M0 236h400M0 264h400M0 292h400"/></g>' +
      '<ellipse cx="202" cy="160" rx="124" ry="98" fill="#000" opacity="0.16"/>' +
      '<ellipse cx="200" cy="150" rx="122" ry="96" fill="' + (o.plate || '#F4EFE6') + '"/>' +
      '<ellipse cx="200" cy="150" rx="98" ry="76" fill="none" stroke="#000" stroke-opacity="0.06" stroke-width="4"/>' +
      o.food +
      '<rect width="400" height="300" fill="url(#v)"/></svg>';
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(s);
  }

  var PHOTO = {
    pork: function () {
      return foodPhoto({
        bg1: '#4A2C18', bg2: '#2A1810',
        food:
          '<g transform="rotate(-7 200 150)">' +
          '<rect x="124" y="108" width="52" height="86" rx="8" fill="#8B4B26"/>' +
          '<rect x="124" y="108" width="52" height="21" rx="8" fill="#E3B673"/>' +
          '<rect x="182" y="102" width="52" height="92" rx="8" fill="#7C4222"/>' +
          '<rect x="182" y="102" width="52" height="21" rx="8" fill="#DCAC69"/>' +
          '<rect x="240" y="110" width="48" height="84" rx="8" fill="#8B4B26"/>' +
          '<rect x="240" y="110" width="48" height="20" rx="8" fill="#E3B673"/></g>' +
          '<circle cx="146" cy="206" r="9" fill="#5E8A46"/><circle cx="256" cy="212" r="7" fill="#6E9A50"/>'
      });
    },
    tikka: function () {
      return foodPhoto({
        bg1: '#53301A', bg2: '#2C1A12', plate: '#EFE7DA',
        food:
          '<ellipse cx="200" cy="150" rx="92" ry="70" fill="#C0521D"/>' +
          '<ellipse cx="200" cy="146" rx="88" ry="66" fill="#D8682A"/>' +
          '<g fill="#9E3D13">' +
          '<rect x="150" y="120" width="34" height="26" rx="10"/><rect x="196" y="112" width="36" height="26" rx="10"/>' +
          '<rect x="168" y="156" width="36" height="26" rx="10"/><rect x="216" y="152" width="32" height="26" rx="10"/>' +
          '</g>' +
          '<path d="M146 172c10-24 44-38 76-30 22 6 32 22 26 34" stroke="#F5E7D2" stroke-width="9" ' +
          'fill="none" stroke-linecap="round" opacity="0.9"/>' +
          '<circle cx="176" cy="188" r="6" fill="#57883F"/><circle cx="232" cy="118" r="5" fill="#57883F"/>'
      });
    },
    greek: function () {
      return foodPhoto({
        bg1: '#4C4128', bg2: '#241E14', plate: '#F6F1E4',
        food:
          '<ellipse cx="200" cy="158" rx="94" ry="66" fill="#E7D8AE"/>' +
          '<g fill="#DCC894" opacity="0.9">' +
          '<rect x="132" y="140" width="26" height="8" rx="4"/><rect x="238" y="150" width="26" height="8" rx="4"/>' +
          '<rect x="180" y="192" width="26" height="8" rx="4"/></g>' +
          '<path d="M150 122c22-22 62-24 84-6 16 14 12 40-8 50-24 12-58 6-72-12-8-12-8-24-4-32z" fill="#B4762F"/>' +
          '<path d="M162 126c18-14 48-16 66-2" stroke="#8E5A20" stroke-width="6" fill="none" stroke-linecap="round"/>' +
          '<path d="M254 176a26 26 0 0126 0 26 26 0 01-26 0z" fill="#F0C33C"/>' +
          '<path d="M118 172a24 24 0 0124 0 24 24 0 01-24 0z" fill="#F0C33C"/>' +
          '<circle cx="212" cy="196" r="6" fill="#4F7B58"/>'
      });
    },
    pie: function () {
      return foodPhoto({
        bg1: '#3D2A1B', bg2: '#211610', plate: '#EDE5D6',
        food:
          '<circle cx="200" cy="148" r="86" fill="#C68B3C"/>' +
          '<circle cx="200" cy="144" r="78" fill="#DFA954"/>' +
          '<circle cx="200" cy="144" r="60" fill="none" stroke="#B87C30" stroke-width="5" opacity="0.7"/>' +
          '<g stroke="#A96F28" stroke-width="6" stroke-linecap="round">' +
          '<path d="M186 128l28 12M186 152l28 12"/></g>' +
          '<g fill="#B87C30">' +
          '<circle cx="132" cy="112" r="9"/><circle cx="268" cy="112" r="9"/>' +
          '<circle cx="132" cy="180" r="9"/><circle cx="268" cy="180" r="9"/>' +
          '<circle cx="200" cy="70" r="9"/><circle cx="200" cy="222" r="9"/></g>'
      });
    }
  };

  function mockInit() {
    var T = todayStr();
    var nextId = 900;

    function mk(off, title, personId, start, end, extra) {
      var date = addDays(T, off);
      var e = {
        id: 0, source: 'ics', title: title, person_id: personId, location: null,
        all_day: !start, date: date, end_date: date, start: start || null, end: end || null,
        start_utc: null, end_utc: null, editable: false, notes: null, countdown: false, feed: 'Family'
      };
      if (extra) for (var k in extra) e[k] = extra[k];
      if (e.source === 'local') { e.editable = true; delete e.feed; e.id = nextId++; }
      else { e.id = 'ics:' + e.feed + ':uid' + (nextId++); }
      e.start_utc = utcish(e.date, e.start);
      e.end_utc = utcish(e.end_date, e.end);
      return e;
    }

    function utcish(date, hm) {          // Brisbane is UTC+10, no DST
      if (!hm) return null;
      var p = date.split('-').map(Number), q = hm.split(':').map(Number);
      var d = new Date(Date.UTC(p[0], p[1] - 1, p[2], q[0] - 10, q[1]));
      return d.toISOString().replace('.000', '');
    }

    var events = [];

    // the weekday rhythm: school runs and stand-ups, -14 .. +45 days
    for (var off = -14; off <= 45; off++) {
      var d = addDays(T, off), w = dowOf(d);
      if (w >= 1 && w <= 5) {
        events.push(mk(off, 'School drop-off', 'kid', '08:30', '08:50', { feed: 'Family' }));
        events.push(mk(off, 'School pick-up', 'partner', '15:00', '15:20', { feed: 'Family' }));
        if (w !== 3) events.push(mk(off, 'Team stand-up', 'rohan', '09:15', '09:30', { feed: 'Work' }));
      }
    }

    // this week's texture
    events.push(mk(0, 'Site visit — Ipswich rail bridge', 'rohan', '10:30', '13:00',
      { feed: 'Work', location: 'Ipswich' }));
    events.push(mk(0, 'Swim lesson', 'kid', '16:15', '17:00', { feed: 'Family', location: 'Corinda pool' }));
    events.push(mk(0, 'Bins out — yellow lid', 'family', '19:30', '19:45',
      { source: 'local', notes: 'Recycling week.' }));
    events.push(mk(0, 'Pilates', 'partner', '18:30', '19:30', { feed: 'Partner' }));

    events.push(mk(1, 'Dentist — checkup', 'kid', '09:30', '10:15',
      { source: 'local', location: 'Sherwood Dental', notes: 'Bring the referral letter.' }));
    events.push(mk(1, 'Lunch with Sam', 'partner', '12:00', '13:00', { feed: 'Partner' }));
    events.push(mk(1, 'Soccer training', 'kid', '17:30', '18:30', { feed: 'Family', location: 'Oxley oval' }));
    events.push(mk(1, 'Design review — Bremer culvert', 'rohan', '14:00', '15:30', { feed: 'Work' }));

    events.push(mk(2, 'Book week — dress up day', 'kid', null, null, { feed: 'Family' }));
    events.push(mk(2, 'Client workshop', 'rohan', '09:00', '12:00', { feed: 'Work', location: 'Brisbane CBD' }));
    events.push(mk(2, 'Guitar lesson', 'kid', '16:00', '16:45', { feed: 'Family' }));
    /* Wednesday morning is the week's pile-up — four things at once, which is
       what the time grid has to survive: three lanes and a "+N" chip. */
    events.push(mk(2, 'Physio', 'partner', '09:30', '10:30', { feed: 'Partner' }));
    events.push(mk(2, 'Vet — dog vaccination', 'family', '10:00', '10:45',
      { source: 'local', location: 'Corinda vet' }));
    events.push(mk(2, 'Council call — culvert RFI', 'rohan', '10:15', '11:00', { feed: 'Work' }));

    events.push(mk(3, 'Date night', 'family', '18:30', '21:30',
      { source: 'local', countdown: true, location: 'Sherwood', notes: 'Nan is babysitting.' }));
    events.push(mk(3, 'Book club', 'partner', '19:00', '20:30', { feed: 'Partner' }));
    events.push(mk(4, 'School assembly', 'kid', '09:00', '10:00', { feed: 'Family' }));
    events.push(mk(4, 'Payday', 'family', null, null, { feed: 'Family' }));

    events.push(mk(5, 'Nippers', 'kid', '07:30', '10:00', { feed: 'Family', location: 'Redcliffe' }));
    events.push(mk(5, 'Grandma visits', 'family', null, null, { source: 'local' }));
    events.push(mk(6, 'Parkrun', 'rohan', '07:00', '08:00', { feed: 'Rohan' }));
    events.push(mk(6, 'Roast lunch at Nan\'s', 'family', '12:00', '15:00', { feed: 'Family' }));

    events.push(mk(9, 'Swim carnival', 'kid', '08:30', '14:00', { source: 'local', countdown: true }));
    events.push(mk(12, 'Camping — Noosa North Shore', 'family', null, null,
      { source: 'local', countdown: true, end_date: addDays(T, 14) }));
    events.push(mk(19, 'Kid\'s birthday party', 'kid', '10:00', '13:00',
      { source: 'local', countdown: true, location: 'Oxley park' }));
    events.push(mk(23, 'Term 3 ends', 'family', null, null, { feed: 'Family' }));

    /* Public holidays, off the kind:"holiday" feed — no person, all day, and
       drawn as chips rather than pills. The Ekka show holiday lands on the
       coming Wednesday (never today, so both surfaces show one). */
    var toWed = (3 - dowOf(T) + 7) % 7 || 7;
    function hol(off, title) {
      events.push(mk(off, title, null, null, null, { feed: 'Holidays' }));
    }
    hol(0, 'King’s Birthday');
    hol(toWed, 'Royal Queensland Show (Brisbane)');
    hol(28, 'Labour Day');

    var lists = [
      {
        id: 1, name: 'Groceries', kind: 'grocery', items: [
          { id: 11, text: 'Milk', done: false, created_at: T },
          { id: 12, text: 'Sourdough', done: false, created_at: T },
          { id: 13, text: 'Beef mince', done: false, created_at: T },
          { id: 14, text: 'Bananas', done: true, created_at: T },
          { id: 15, text: 'Weet-Bix', done: false, created_at: T },
          { id: 16, text: 'Sunscreen', done: false, created_at: T }
        ]
      },
      {
        id: 2, name: 'To-do', kind: 'todo', items: [
          { id: 21, text: 'Book car service', done: false, created_at: T },
          { id: 22, text: 'Pay rates notice', done: false, created_at: T },
          { id: 23, text: 'Fix the gate latch', done: true, created_at: T }
        ]
      },
      {
        id: 3, name: 'School', kind: 'custom', items: [
          { id: 31, text: 'Library books back Thursday', done: false, created_at: T },
          { id: 32, text: 'Book week costume', done: false, created_at: T }
        ]
      }
    ];

    /* Hand-entered recipes have source_url null and an empty meta; imported
       ones carry the hostname and the times the parser found. */
    function hand(id, name, notes, ing, meta) {
      return { id: id, name: name, notes: notes, ingredients: ing, source_url: null, meta: meta || {} };
    }
    function web(id, name, notes, ing, slug, meta) {
      return {
        id: id, name: name, notes: notes, ingredients: ing,
        source_url: 'https://www.recipetineats.com/' + slug + '/',
        meta: meta
      };
    }

    /* Every recipe carries a method (API.md meta.steps): the starters get the
       three brisk steps the backend's starter pack ships, the imported
       fixtures get the longer method a real page would have handed over. */
    var recipes = [
      hand(1, 'Spag bol', 'The weeknight workhorse. Simmer longer on weekends if you can.',
        ['500g beef mince', '1 brown onion, diced', '2 garlic cloves', '1 grated carrot',
          '700g passata', '2 tbsp tomato paste', 'Spaghetti', 'Parmesan to serve'],
        { servings: '4-5', prep_minutes: 10, cook_minutes: 30, steps: [
          'Brown the mince in a big pot, then soften the onion, garlic and carrot in with it.',
          'Stir in the tomato paste and passata and simmer 20 minutes or longer — longer is better.',
          'Boil the spaghetti, toss it through the sauce and finish with parmesan.'] }),
      hand(2, 'Butter chicken (mild)', 'Kid-mild — add chilli at the table for the grown-ups.',
        ['700g chicken thigh, diced', '3 tbsp butter chicken paste', '400ml cooking cream',
          '400g tin crushed tomatoes', 'Basmati rice', 'Naan or pappadums'],
        { servings: '4', prep_minutes: 10, cook_minutes: 25, steps: [
          'Fry the paste for a minute, then brown the chicken in it.',
          'Add the tomatoes and cream and simmer 20 minutes until it thickens.',
          'Serve over basmati with naan or pappadums.'] }),
      hand(3, 'Tacos', 'Build-your-own keeps everyone happy.',
        ['500g beef mince', '1 packet taco seasoning', 'Taco shells', 'Iceberg lettuce, shredded',
          '2 tomatoes, diced', 'Grated cheese', 'Sour cream', '1 avocado'],
        { servings: '4', prep_minutes: 15, cook_minutes: 15, steps: [
          'Brown the mince, add the seasoning and a splash of water, simmer until sticky.',
          'Warm the shells and put every topping in its own bowl.',
          'Let everyone build their own at the table.'] }),
      hand(4, 'Barra and salad', 'From the fish shop at Sherwood.',
        ['4 barramundi fillets', 'Salad mix', '1 lemon', 'Chat potatoes', 'Olive oil'],
        { servings: '4', prep_minutes: 10, cook_minutes: 15, steps: [
          'Boil the chat potatoes until a knife slides in.',
          'Pan-fry the barra skin-side down 4 minutes, flip for 2 more.',
          'Dress the salad with oil and lemon and plate it all up.'] }),
      hand(5, 'Sausages, mash and gravy', 'Steam some greens while the mash is going.',
        ['8 thick beef sausages', '1kg potatoes', '50g butter', 'Gravy powder', 'Frozen peas'],
        { servings: '4', prep_minutes: 10, cook_minutes: 30, steps: [
          'Boil the potatoes, then mash with plenty of butter.',
          'Brown the sausages slowly so they cook through without splitting.',
          'Make the gravy, steam the peas and serve.'] }),
      hand(6, 'Pumpkin soup', null,
        ['1kg pumpkin', '1L chicken stock', '1 brown onion', '100ml cream', 'Crusty bread'],
        { servings: '4', prep_minutes: 10, cook_minutes: 25, steps: [
          'Soften the onion, add the pumpkin and stock and simmer until the pumpkin collapses.',
          'Blend smooth, then stir the cream through off the heat.',
          'Serve with crusty bread.'] }),
      hand(7, 'Fried rice', 'Best with day-old rice. Clears out the vegetable drawer.',
        ['3 cups cooked rice (cold)', '2 rashers bacon, diced', '2 eggs', '1 cup frozen peas and corn',
          '2 spring onions', '2 tbsp soy sauce', '1 tsp sesame oil'],
        { servings: '4', prep_minutes: 10, cook_minutes: 15, steps: [
          'Scramble the eggs in a hot wok and set them aside.',
          'Fry the bacon, then the vegetables, then the cold rice — keep it moving.',
          'Return the egg, splash in soy and sesame and finish with spring onion.'] }),
      hand(8, 'Homemade pizza night', 'Friday tradition. Shop-bought bases are fine.',
        ['4 pizza bases', 'Passata', '2 cups mozzarella', 'Ham or salami', 'Mushrooms', 'Pineapple'],
        { servings: '4', prep_minutes: 20, cook_minutes: 15, steps: [
          'Heat the oven as hot as it goes with the trays inside.',
          'Sauce the bases thinly, cheese them, then let everyone top their own.',
          'Bake 10-12 minutes until the edges blister.'] }),
      hand(9, 'Roast chicken and veg', 'Sunday. Keep the carcass for stock.',
        ['1.6kg whole chicken', '1kg potatoes', '2 carrots', '1 pumpkin wedge', 'Butter', 'Rosemary'],
        { servings: '5', prep_minutes: 15, cook_minutes: 90, steps: [
          'Butter the chicken all over, season it hard and sit it in a hot oven (200°C).',
          'Add the chopped vegetables around it after 20 minutes and roast another hour.',
          'Rest the bird 10 minutes before carving — it matters more than the cooking.'] }),
      hand(10, 'Chicken stir-fry', 'Sauce is 2:1:1 soy, oyster sauce, honey.',
        ['600g chicken breast, sliced', '1 broccoli head', '1 carrot', '1 capsicum',
          '2 tbsp soy sauce', '1 tbsp oyster sauce', '1 tbsp honey', 'Rice or hokkien noodles'],
        { servings: '4', prep_minutes: 15, cook_minutes: 12, steps: [
          'Mix the sauce first — there is no time once the wok is going.',
          'Sear the chicken in batches, then lift it out and fry the vegetables hard.',
          'Everything back in, sauce over, toss for a minute and serve on rice or noodles.'] }),

      web(11, 'Crispy Slow Roasted Pork Belly',
        'Ridiculously crispy crackling every single time, with juicy tender flesh underneath.',
        ['1.5 kg / 3 lb pork belly, skin on', '2 tsp cooking salt', '1 tbsp olive oil',
          '1 tsp fennel seeds', '2 garlic cloves, minced', 'Black pepper'],
        'crispy-slow-roasted-pork-belly',
        { servings: '6', prep_minutes: 15, cook_minutes: 165, total_minutes: 180,
          image_cached: true, image_data: PHOTO.pork(), author: 'Nagi', steps: [
            'Dry the skin thoroughly with paper towel, then leave it uncovered in the fridge overnight if you can.',
            'Rub the flesh (not the skin) with oil, garlic, fennel and pepper.',
            'Sit the belly on a rack, skin up, and rub the skin with salt only.',
            'Slow roast at 160°C for 2 1/2 hours — the flesh goes meltingly tender.',
            'Crank the oven to 240°C for 20-30 minutes until the skin puffs and crackles right across.',
            'Rest 10 minutes, then cut with a serrated knife, crackling side down.'] }),
      web(12, 'Chicken Tikka Masala',
        'A restaurant-standard tikka masala you can make on a Tuesday. Marinate ahead if you can.',
        ['800g chicken thigh, diced', '1 cup plain yoghurt', '2 tbsp tikka masala paste',
          '400g tin crushed tomatoes', '1 brown onion, diced', '3 garlic cloves',
          '1 tbsp fresh ginger, grated', '150ml cream', 'Basmati rice, to serve', 'Coriander'],
        'chicken-tikka-masala',
        { servings: '4-5', prep_minutes: 20, cook_minutes: 40, total_minutes: 60,
          image_cached: true, image_data: PHOTO.tikka(), author: 'Nagi', steps: [
            'Marinate the chicken in the yoghurt and half the paste — 20 minutes, or overnight.',
            'Sear the chicken in a hot pan in batches until charred at the edges, then set aside.',
            'Soften the onion, garlic and ginger, then fry the rest of the paste until fragrant.',
            'Add the tomatoes and simmer 15 minutes until the sauce darkens.',
            'Return the chicken with the cream and simmer another 10 minutes.',
            'Finish with coriander and serve over basmati.'] }),
      web(13, 'One Pot Greek Chicken and Lemon Rice',
        'The rice cooks in the chicken juices. One pan, no fuss, big flavour.',
        ['6 chicken thigh cutlets, skin on', '1 1/2 cups long grain rice',
          '2 3/4 cups chicken stock', '1 lemon, juiced', '3 garlic cloves, minced',
          '2 tsp dried oregano', '1 tbsp olive oil', 'Salt and pepper'],
        'greek-chicken-lemon-rice',
        { servings: '5', prep_minutes: 10, cook_minutes: 45, total_minutes: 55,
          image_cached: true, image_data: PHOTO.greek(), author: 'Nagi', steps: [
            'Toss the chicken with oil, oregano, lemon, salt and pepper.',
            'Brown it skin-side down in an ovenproof pan, then lift it out.',
            'Fry the garlic and rice in the chicken fat for a minute.',
            'Pour in the stock, sit the chicken back on top skin-side up.',
            'Bake uncovered at 180°C for 35 minutes until the rice has drunk the stock.',
            'Rest 5 minutes with a lid on, then fluff the rice around the chicken.'] }),
      web(14, 'Beef and Guinness Pie',
        'Slow-cooked beef in a rich stout gravy under golden puff pastry. Weekend cooking.',
        ['1.2 kg chuck beef, cut into 3cm cubes', '2 tbsp plain flour', '440ml Guinness',
          '2 cups beef stock', '2 carrots, diced', '1 brown onion', '2 tbsp tomato paste',
          '3 sheets puff pastry', '1 egg, for brushing'],
        'beef-and-guinness-pie',
        { servings: '6', prep_minutes: 30, cook_minutes: 150, total_minutes: 180,
          image_cached: true, image_data: PHOTO.pie(), author: 'Nagi', steps: [
            'Toss the beef in the flour and brown it hard in batches — crowding the pot steams it.',
            'Soften the onion and carrot, stir in the tomato paste.',
            'Deglaze with the Guinness, scraping the pot, then add the stock and the beef.',
            'Cover and simmer very gently for 2 hours until the beef shreds under a fork.',
            'Cool the filling (hot filling melts pastry), then fill the pie dish.',
            'Lid with puff pastry, brush with egg, cut a steam vent.',
            'Bake at 200°C for 30 minutes until deep golden.'] })
    ];

    var mon = mondayOf(T);
    var plan = {};
    [[0, 12], [1, 3], [2, 2], [3, null], [4, 5], [5, 4], [6, 9]].forEach(function (pair, i) {
      var date = addDays(mon, i);
      plan[date] = {
        dinner: pair[1] ? { recipe_id: pair[1], recipe_name: recipeName(recipes, pair[1]), free_text: null }
          : { recipe_id: null, recipe_name: null, free_text: 'Leftovers' },
        lunch: i < 5 ? { recipe_id: null, recipe_name: null, free_text: 'School lunchbox' } : null,
        other: null
      };
    });

    var chores = [
      { id: 1, title: 'Make your bed', person_id: 'kid', days: 'MO,TU,WE,TH,FR,SA,SU', slot: 'morning', stars: 1 },
      { id: 2, title: 'Feed the dog', person_id: 'kid', days: 'MO,TU,WE,TH,FR,SA,SU', slot: 'morning', stars: 1 },
      { id: 3, title: 'Pack the school bag', person_id: 'kid', days: 'MO,TU,WE,TH,FR', slot: 'evening', stars: 1 },
      { id: 4, title: 'Tidy the toys', person_id: 'kid', days: 'MO,WE,FR,SU', slot: 'afternoon', stars: 2 },
      { id: 5, title: 'Bins out', person_id: 'rohan', days: 'MO', slot: 'evening', stars: 1 },
      { id: 6, title: 'Water the herbs', person_id: 'partner', days: 'TU,SA', slot: 'afternoon', stars: 1 },
      { id: 7, title: 'Reading — 15 minutes', person_id: 'kid', days: 'MO,TU,WE,TH,FR,SA,SU', slot: 'evening', stars: 1 },
      { id: 8, title: 'Unpack the dishwasher', person_id: 'partner', days: 'MO,TU,WE,TH,FR,SA,SU', slot: 'morning', stars: 1 }
    ];

    var imports = [
      {
        id: 1,
        received_at: addDays(T, 0) + ' 07:12',
        from_addr: 'newsletter@corindass.eq.edu.au',
        subject: 'Corinda State School — Term 3, Week 6 newsletter',
        body_excerpt: 'Book week is coming up. Prep students will parade in the hall on Wednesday morning. ' +
          'A reminder that the athletics carnival has moved to Friday 4 September, and library books are due back Thursday.',
        events: [
          { index: 0, title: 'Book week parade', date: addDays(T, 2), start_time: '09:00', end_time: '10:00', all_day: false, person_hint: 'kid', location: 'School hall', notes: null },
          { index: 1, title: 'Athletics carnival', date: addDays(T, 18), start_time: '08:30', end_time: '14:30', all_day: false, person_hint: 'kid', location: 'Oxley athletics track', notes: 'Hat and water bottle.' },
          { index: 2, title: 'Library books due', date: addDays(T, 3), start_time: null, end_time: null, all_day: true, person_hint: 'kid', location: null, notes: null }
        ]
      },
      {
        id: 2,
        received_at: addDays(T, -1) + ' 18:40',
        from_addr: 'admin@corindaswimclub.org.au',
        subject: 'Swim club — presentation night and last lesson',
        body_excerpt: 'Presentation night is on Friday 5 September at the clubhouse from 6 pm. ' +
          'Please note the final lesson for the term is the week before.',
        events: [
          { index: 0, title: 'Swim club presentation night', date: addDays(T, 19), start_time: '18:00', end_time: '20:00', all_day: false, person_hint: null, location: 'Corinda pool clubhouse', notes: null },
          { index: 1, title: 'Last swim lesson of term', date: addDays(T, 14), start_time: '16:15', end_time: '17:00', all_day: false, person_hint: 'kid', location: null, notes: null }
        ]
      }
    ];

    var tiles = [
      {
        entity: 'device_tracker.rohan', label: 'Rohan', type: 'presence', allow_action: false,
        state: 'home', attrs: { home: true }
      },
      /* Three Sensibos, which is what the house actually has — the tiles stay
         read-outs and the whole-house sheet carries the controls. */
      {
        entity: 'climate.living_sensibo', label: 'Living', type: 'climate', allow_action: true,
        state: 'cool', attrs: { current_temp: 24.5, setpoint: 23, hvac_mode: 'cool' }
      },
      {
        entity: 'climate.master_sensibo', label: 'Master bed', type: 'climate', allow_action: true,
        state: 'heat', attrs: { current_temp: 19.2, setpoint: 21, hvac_mode: 'heat' }
      },
      {
        entity: 'climate.study_sensibo', label: 'Study', type: 'climate', allow_action: true,
        state: 'off', attrs: { current_temp: 22.8, setpoint: 24, hvac_mode: 'off' }
      },
      {
        entity: 'cover.bedroom_curtains', label: 'Curtains', type: 'cover', allow_action: true,
        state: 'open', attrs: {}
      },
      {
        entity: 'switch.porch_light', label: 'Porch light', type: 'switch', allow_action: true,
        state: 'off', attrs: {}
      },
      {
        entity: 'media_player.living_sonos', label: 'Sonos', type: 'media', allow_action: true,
        state: 'playing',
        attrs: {
          title: 'Gold Chains', artist: 'Angus & Julia Stone', volume: 0.34, playing: true,
          source_list: FAVOURITES.slice(), source: 'Kitchen Disco', entity_picture: null
        }
      },
      {
        entity: 'fan.bedroom_dyson', label: 'Dyson', type: 'fan', allow_action: true,
        state: 'on', attrs: { on: true, percentage: 40, oscillating: true, preset_mode: 'auto' }
      },
      /* Sungrow SH + SBR: a sunny Brisbane afternoon — the roof carrying the
         house, topping the battery up and still selling nearly 3 kW back.
         attrs follow API.md: watts, battery_w +ve = charging, grid_w +ve =
         exporting. mockSolarDrift() nudges these on every poll. */
      {
        entity: 'solar', label: 'Solar', type: 'solar', allow_action: false,
        state: 'ok',
        attrs: { pv_w: 5200, load_w: 1100, grid_w: 2900, battery_w: 1200, battery_pct: 84 }
      },
      {
        entity: 'sensor.outside_temp', label: 'Outside', type: 'sensor', allow_action: false,
        state: '26.3', attrs: { value: 26.3, unit: '°C' }
      },
      /* Four DVR channels. Garage is deliberately unavailable — a channel
         dropping out is the normal failure on this kind of box, and the wall
         has to say "no signal" rather than show a hole. */
      {
        entity: 'camera.front_yard', label: 'Front yard', type: 'camera_snapshot', allow_action: false,
        state: 'idle', attrs: { snapshot_url: CAM_SCENE.front() }
      },
      {
        entity: 'camera.driveway', label: 'Driveway', type: 'camera_snapshot', allow_action: false,
        state: 'idle', attrs: { snapshot_url: CAM_SCENE.drive() }
      },
      {
        entity: 'camera.back_deck', label: 'Back deck', type: 'camera_snapshot', allow_action: false,
        state: 'idle', attrs: { snapshot_url: CAM_SCENE.deck() }
      },
      {
        entity: 'camera.garage', label: 'Garage', type: 'camera_snapshot', allow_action: false,
        state: 'unavailable', attrs: { snapshot_url: CAM_SCENE.garage() }
      }
    ];

    var wxCodes = [1, 2, 80, 61, 3, 0, 1];
    var sunrises = ['06:12', '06:11', '06:10', '06:09', '06:08', '06:08', '06:07'];
    var sunsets = ['17:28', '17:29', '17:29', '17:30', '17:31', '17:31', '17:32'];
    var daily = [];
    for (var i = 0; i < 7; i++) {
      daily.push({
        date: addDays(T, i),
        min: [14, 15, 16, 14, 13, 12, 14][i],
        max: [26, 27, 24, 22, 25, 27, 28][i],
        rain_prob: [10, 20, 70, 80, 15, 5, 5][i],
        uv: [7, 8, 5, 4, 7, 8, 9][i],
        code: wxCodes[i],
        description: null,
        sunrise: sunrises[i],
        sunset: sunsets[i]
      });
    }

    M = {
      today: T, events: events, lists: lists, recipes: recipes, plan: plan,
      chores: chores, done: {}, redeemed: { kid: 6, rohan: 0, partner: 0, family: 0 },
      imports: imports, tiles: tiles,
      weather: {
        available: true, updated_at: T + 'T07:00:00+10:00',
        current: { temp: 25.8, code: 1, description: 'Mostly clear' },
        sun: { sunrise: sunrises[0], sunset: sunsets[0] },
        daily: daily
      },
      nextId: nextId, nextItemId: 500, nextRecipeId: 20, nextListId: 10, trackIx: 0,
      assistIx: 0
    };

    // a plausible amount of the week already ticked off
    M.done[key(1, addDays(T, -1))] = true;
    M.done[key(2, addDays(T, -1))] = true;
    M.done[key(1, addDays(T, -2))] = true;
    M.done[key(2, T)] = true;
  }

  function key(choreId, date) { return choreId + '|' + date; }

  function recipeName(recipes, id) {
    for (var i = 0; i < recipes.length; i++) if (recipes[i].id === id) return recipes[i].name;
    return null;
  }

  /* --------------------------------------------------------- demo cameras -- */
  /* Four channels off a Swann DVR, which is what the house will actually have
     once it is tied in: two daylight views, one after dark, one in the garage.
     Drawn as data URIs so ?mock=1 never reaches outside for an image, and
     each scene is deliberately different so the cycling thumbnail and the
     camera wall are obviously showing four different places. */

  function camFrame(o) {
    var s =
      '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="300" viewBox="0 0 480 300">' +
      '<rect width="480" height="300" fill="' + o.ground + '"/>' + o.scene +
      /* the DVR's own burn-in: channel on the left, clock on the right */
      '<rect y="0" width="480" height="26" fill="#000" opacity="0.42"/>' +
      '<text x="12" y="18" font-family="monospace" font-size="14" fill="#D8DEE6">' + o.ch + '</text>' +
      '<text x="468" y="18" text-anchor="end" font-family="monospace" font-size="14" ' +
      'fill="#D8DEE6">' + o.clock + '</text>' +
      (o.night ? '<rect width="480" height="300" fill="#0B1830" opacity="0.42"/>' : '') +
      '</svg>';
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(s);
  }

  var CAM_SCENE = {
    /* CH1 — front yard: fence, path to the letterbox, a jacaranda */
    front: function () {
      return camFrame({
        ch: 'CH1 FRONT YARD', clock: '15:42:07', ground: '#3E5A3A',
        scene:
          '<rect width="480" height="128" fill="#8FB0C9"/>' +
          '<rect y="128" width="480" height="172" fill="#4A6B44"/>' +
          '<path d="M196 300l24-172h40l60 172z" fill="#9A9182"/>' +
          '<g fill="#6E7A5E"><rect x="0" y="120" width="480" height="7"/></g>' +
          '<g fill="#B9AE96">' +
          '<rect x="24" y="112" width="9" height="46"/><rect x="52" y="112" width="9" height="46"/>' +
          '<rect x="80" y="112" width="9" height="46"/><rect x="108" y="112" width="9" height="46"/>' +
          '<rect x="136" y="112" width="9" height="46"/><rect x="18" y="122" width="132" height="7"/></g>' +
          '<rect x="236" y="150" width="12" height="52" fill="#5A5348"/>' +
          '<rect x="222" y="132" width="40" height="24" rx="4" fill="#C4553A"/>' +
          '<circle cx="392" cy="86" r="52" fill="#4C6B8C"/><circle cx="352" cy="104" r="34" fill="#42607F"/>' +
          '<rect x="382" y="132" width="14" height="60" fill="#4A4038"/>'
      });
    },
    /* CH2 — driveway: slab, roller door, the car nose-in, two bins */
    drive: function () {
      return camFrame({
        ch: 'CH2 DRIVEWAY', clock: '15:42:07', ground: '#8A8A86',
        scene:
          '<rect width="480" height="112" fill="#9EB6C6"/>' +
          '<rect y="112" width="480" height="188" fill="#8E8E8A"/>' +
          '<path d="M120 300L188 112h104l68 188z" fill="#A5A5A0"/>' +
          '<rect x="150" y="34" width="200" height="86" fill="#B6AE9C"/>' +
          '<rect x="164" y="52" width="172" height="66" fill="#8D8878"/>' +
          '<g stroke="#7C7767" stroke-width="3"><path d="M164 66h172M164 82h172M164 98h172"/></g>' +
          '<rect x="186" y="176" width="120" height="52" rx="12" fill="#33475E"/>' +
          '<rect x="204" y="152" width="84" height="34" rx="10" fill="#3E556F"/>' +
          '<rect x="212" y="158" width="68" height="22" rx="6" fill="#8FA6B8"/>' +
          '<circle cx="206" cy="230" r="14" fill="#20262C"/><circle cx="288" cy="230" r="14" fill="#20262C"/>' +
          '<rect x="396" y="176" width="44" height="62" rx="5" fill="#3E6B45"/>' +
          '<rect x="392" y="168" width="52" height="12" rx="4" fill="#2F5335"/>' +
          '<rect x="344" y="186" width="42" height="52" rx="5" fill="#7A4A2E"/>' +
          '<rect x="340" y="178" width="50" height="12" rx="4" fill="#603A24"/>'
      });
    },
    /* CH3 — back deck, after dark: boards, railing, two chairs, one lamp */
    deck: function () {
      return camFrame({
        ch: 'CH3 BACK DECK', clock: '21:07:33', ground: '#1B2430', night: true,
        scene:
          '<rect width="480" height="120" fill="#16233A"/>' +
          '<circle cx="404" cy="58" r="17" fill="#C9D6E8" opacity="0.8"/>' +
          '<rect y="120" width="480" height="180" fill="#5A4A38"/>' +
          '<g stroke="#42362A" stroke-width="4">' +
          '<path d="M0 150h480M0 182h480M0 214h480M0 246h480M0 278h480"/></g>' +
          '<g fill="#6B5A44"><rect x="30" y="96" width="10" height="56"/>' +
          '<rect x="150" y="96" width="10" height="56"/><rect x="270" y="96" width="10" height="56"/>' +
          '<rect x="390" y="96" width="10" height="56"/><rect x="0" y="104" width="480" height="9"/></g>' +
          '<rect x="70" y="164" width="76" height="60" rx="8" fill="#3C3A46"/>' +
          '<rect x="70" y="150" width="76" height="24" rx="8" fill="#494654"/>' +
          '<rect x="190" y="172" width="88" height="18" rx="6" fill="#4A4536"/>' +
          '<rect x="222" y="188" width="10" height="42" fill="#3E3A2E"/>' +
          '<circle cx="336" cy="164" r="26" fill="#E8B95C" opacity="0.55"/>' +
          '<rect x="328" y="158" width="16" height="22" rx="4" fill="#F2D28A"/>'
      });
    },
    /* CH4 — inside the garage: shelving, a bike, the car's back end */
    garage: function () {
      return camFrame({
        ch: 'CH4 GARAGE', clock: '15:42:07', ground: '#2A2C30',
        scene:
          '<rect width="480" height="300" fill="#33363C"/>' +
          '<rect y="196" width="480" height="104" fill="#43464C"/>' +
          '<g fill="#5A5044"><rect x="18" y="72" width="140" height="10"/>' +
          '<rect x="18" y="126" width="140" height="10"/><rect x="18" y="180" width="140" height="10"/>' +
          '<rect x="18" y="72" width="9" height="118"/><rect x="149" y="72" width="9" height="118"/></g>' +
          '<g fill="#7E6A4C"><rect x="30" y="46" width="30" height="26"/><rect x="68" y="52" width="24" height="20"/>' +
          '<rect x="100" y="42" width="34" height="30"/><rect x="34" y="100" width="40" height="26"/>' +
          '<rect x="86" y="104" width="28" height="22"/></g>' +
          '<circle cx="212" cy="212" r="30" fill="none" stroke="#6C7078" stroke-width="5"/>' +
          '<circle cx="286" cy="212" r="30" fill="none" stroke="#6C7078" stroke-width="5"/>' +
          '<path d="M212 212l32-56h26M244 156l42 56" stroke="#8A6A3C" stroke-width="6" fill="none"/>' +
          '<rect x="336" y="126" width="132" height="86" rx="10" fill="#4B3A3A"/>' +
          '<rect x="348" y="140" width="108" height="34" rx="6" fill="#6E5A5A"/>' +
          '<rect x="342" y="192" width="30" height="12" rx="4" fill="#C24A3A"/>' +
          '<rect x="432" y="192" width="30" height="12" rx="4" fill="#C24A3A"/>'
      });
    }
  };

  function mockChoresFor(date) {
    var code = DOW_CODE[dowOf(date)];
    var weekStart = mondayOf(date);
    return people().map(function (p) {
      var mine = M.chores.filter(function (c) {
        return c.person_id === p.id && c.days.split(',').indexOf(code) !== -1;
      }).sort(function (a, b) {
        var order = { morning: 0, afternoon: 1, evening: 2 };
        return order[a.slot] - order[b.slot];
      });
      var earned = 0;
      M.chores.filter(function (c) { return c.person_id === p.id; }).forEach(function (c) {
        for (var i = 0; i < 7; i++) {
          if (M.done[key(c.id, addDays(weekStart, i))]) earned += c.stars || 1;
        }
      });
      return {
        id: p.id, name: p.name, colour: p.colour, stars_week: earned,
        chores: mine.map(function (c) {
          return { id: c.id, title: c.title, slot: c.slot, stars: c.stars, done: !!M.done[key(c.id, date)] };
        })
      };
    });
  }

  /* The demo inverter. Daytime is a sunny Brisbane afternoon that wanders a
     little on every 15 s poll so the overlay's arms breathe; at night — which
     the demo reaches through its own theme toggle — the roof is dark, the
     battery is running the house and a couple of hundred watts trickle in off
     the mains, so the flow diagram reverses in front of you.

     Grid is never invented: it is whatever the balance leaves over, so the
     four numbers always agree (pv - load - battery = grid, +ve = exporting). */
  function mockSolarDrift() {
    var t = M.tiles.filter(function (x) { return x.type === 'solar'; })[0];
    if (!t || M.solarFrozen) return;      // frozen: tests pin a state to look at
    if (!M.solarPct) M.solarPct = { day: 84, night: 62 };
    var night = document.documentElement.getAttribute('data-theme') === 'night';
    var jit = function (span) { return (Math.random() * 2 - 1) * span; };
    var clamp = function (v, lo, hi) { return Math.max(lo, Math.min(hi, v)); };
    var r10 = function (v) { return Math.round(v / 10) * 10; };
    var pv, load, bat;

    if (night) {
      pv = 0;
      load = clamp(1100 + jit(250), 350, 2400);
      /* never let the battery out-run the house: at night the demo should
         be pulling a little off the mains, not exporting in the dark */
      bat = -clamp(900 + jit(200), 200, Math.max(200, load - 150));
      M.solarPct.night = clamp(M.solarPct.night - 0.09, 18, 100);
      t.attrs.battery_pct = Math.round(M.solarPct.night * 10) / 10;
    } else {
      pv = clamp(5200 + jit(300), 0, 9000);
      load = clamp(1100 + jit(250), 350, 3000);
      bat = clamp(1200 + jit(200), 0, Math.max(0, pv - load));   // charging
      M.solarPct.day = clamp(M.solarPct.day + 0.08, 0, 97);
      t.attrs.battery_pct = Math.round(M.solarPct.day * 10) / 10;
    }
    t.attrs.pv_w = r10(pv);
    t.attrs.load_w = r10(load);
    t.attrs.battery_w = r10(bat);
    t.attrs.grid_w = r10(pv - load - bat);
    t.state = 'ok';
  }

  /* What the demo microphone "hears" — one line per tap, in order, so a demo
     run shows the two interesting answers before it shows the polite one. */
  var MOCK_HEARD = [
    "What's on today?",
    'Turn on the porch light',
    'Is it going to rain tomorrow?'
  ];

  /* One answer function for both the typed and the spoken path, because the
     backend has one too. */
  function mockAssistAnswer(said) {
    var q = said.toLowerCase();

    if (/porch|light/.test(q)) {
      var on = !/\b(off|out)\b/.test(q);
      var tile = null;
      for (var i = 0; i < M.tiles.length; i++) {
        if (M.tiles[i].entity === 'switch.porch_light') tile = M.tiles[i];
      }
      if (tile) {
        tile.state = on ? 'on' : 'off';
        loadHa().then(renderSoon);
      }
      return on ? 'Porch light on.' : 'Porch light off.';
    }

    if (/today|on today|what's on|whats on|agenda|calendar/.test(q)) {
      var evs = (D.byDate[state.today] || []).filter(function (e) {
        return !isHoliday(e);
      });
      if (!evs.length) return 'Nothing on the calendar today.';
      var next = null, n = nowMins();
      for (var j = 0; j < evs.length; j++) {
        if (!evs[j].all_day && mins(evs[j].start) > n) { next = evs[j]; break; }
      }
      return evs.length + (evs.length === 1 ? ' thing' : ' things') + ' on today' +
        (next ? '. Next up is ' + next.title + ' at ' + fmtTime(next.start) + '.' : '.');
    }

    if (/rain|weather|hot|cold|temp/.test(q)) {
      var d1 = (M.weather && M.weather.daily && M.weather.daily[1]) || null;
      return d1
        ? 'Tomorrow looks like ' + Math.round(d1.min) + ' to ' + Math.round(d1.max) +
          ' degrees, ' + (d1.rain_prob || 0) + ' per cent chance of rain.'
        : 'I do not have the forecast just now.';
    }

    return 'I heard you, but that one is not wired up in the demo. Try ' +
      '“what’s on today” or “turn on the porch light”.';
  }

  function mockRequest(method, url, body) {
    // fetching and parsing a real recipe page takes a beat — mimic it, so the
    // "Importing…" state is actually exercised in the demo.
    var delay = url.indexOf('/api/recipes/import') === 0 ? 800 : 45;
    return new Promise(function (resolve, reject) {
      setTimeout(function () {
        try { resolve(mockRoute(method, url, body)); }
        catch (err) { reject(err); }
      }, delay);
    });
  }

  function mockRoute(method, url, body) {
    var parts = url.split('?');
    var path = parts[0].replace(/\/+$/, '');
    var q = new URLSearchParams(parts[1] || '');
    var seg = path.split('/').filter(Boolean);      // ['api', ...]

    if (!M && path !== '/api/status') mockInit();

    /* ---- status ---- */
    if (path === '/api/status') {
      var stamp = new Date().toISOString().slice(0, 19) + 'Z';
      var res = {
        version: '0.1.0-demo',
        now_utc: stamp,
        today_local: todayStr(),
        jobs: {
          ics: { at: stamp, ok: true, detail: '4 feeds, 214 events' },
          imap: { at: stamp, ok: true, detail: '2 pending' },
          weather: { at: stamp, ok: true, detail: 'open-meteo' },
          ha: { at: stamp, ok: true, detail: '7 entities' }
        },
        feeds: {
          Rohan: { at: stamp, ok: true, detail: '38 events' },
          Work: { at: new Date(Date.now() - 5400000).toISOString().slice(0, 19) + 'Z', ok: true, detail: 'subscribed feed, lags' },
          Partner: { at: stamp, ok: true, detail: '21 events' },
          Family: { at: stamp, ok: true, detail: '96 events' },
          Holidays: { at: stamp, ok: true, detail: '31 events' }
        },
        config: {
          people: [
            { id: 'rohan', name: 'Rohan', colour: '#3D5A80' },
            { id: 'partner', name: 'Mel', colour: '#7C5CBF' },
            { id: 'kid', name: 'Ivy', colour: '#E8A13D' },
            { id: 'family', name: 'Family', colour: '#4F7B58' }
          ],
          screen: { sleep: '21:00', wake: '05:45' },
          /* the holiday feed is what makes the chips a distinct kind */
          feeds: [
            { name: 'Rohan', person: 'rohan', kind: 'calendar' },
            { name: 'Work', person: 'rohan', kind: 'calendar' },
            { name: 'Partner', person: 'partner', kind: 'calendar' },
            { name: 'Family', person: 'family', kind: 'calendar' },
            { name: 'Holidays', person: null, kind: 'holiday' }
          ],
          ha_tiles: []
        }
      };
      if (!M) { D.status = res; mockInit(); }
      return res;
    }

    /* ---- agenda + events ---- */
    if (path === '/api/agenda') {
      var from = q.get('from'), to = q.get('to');
      var evs = M.events.filter(function (e) {
        return (e.end_date || e.date) >= from && e.date <= to;
      }).slice().sort(function (a, b) {
        if (a.date !== b.date) return a.date < b.date ? -1 : 1;
        return evCmp(a, b);
      });
      return { from: from, to: to, events: evs, last_sync: new Date().toISOString() };
    }

    if (path === '/api/events' && method === 'POST') {
      var ne = {
        id: M.nextId++, source: 'local', title: body.title, person_id: body.person_id || null,
        location: body.location || null, all_day: !!body.all_day, date: body.date,
        end_date: body.end_date || body.date, start: body.all_day ? null : (body.start || '09:00'),
        end: body.all_day ? null : (body.end || null), start_utc: null, end_utc: null,
        editable: true, notes: body.notes || null, countdown: !!body.countdown
      };
      if (!ne.all_day && !ne.end) {
        var h = mins(ne.start) + 60;
        ne.end = pad2(Math.floor(h / 60) % 24) + ':' + pad2(h % 60);
      }
      M.events.push(ne);
      return ne;
    }

    if (seg[1] === 'events' && seg[2]) {
      var idx = -1;
      for (var i = 0; i < M.events.length; i++) if (String(M.events[i].id) === seg[2]) idx = i;
      if (idx < 0 || M.events[idx].source !== 'local') { var e404 = new Error('Not found'); e404.status = 404; throw e404; }
      if (method === 'DELETE') { M.events.splice(idx, 1); return { ok: true }; }
      if (method === 'PATCH') {
        var t = M.events[idx];
        Object.keys(body).forEach(function (k) { t[k] = body[k]; });
        if (t.all_day) { t.start = null; t.end = null; }
        t.end_date = t.end_date || t.date;
        return t;
      }
    }

    /* ---- chores + stars ---- */
    if (path === '/api/chores/today') {
      var date = q.get('date') || todayStr();
      return { date: date, weekday: DOW_CODE[dowOf(date)], people: mockChoresFor(date) };
    }

    if (path === '/api/chores' && method === 'GET') return { chores: M.chores };

    if (path === '/api/chores' && method === 'POST') {
      var nc = {
        id: Math.max.apply(null, M.chores.map(function (c) { return c.id; })) + 1,
        title: body.title, person_id: body.person_id, days: body.days,
        slot: body.slot, stars: body.stars || 1
      };
      M.chores.push(nc);
      return nc;
    }

    if (seg[1] === 'chores' && seg[2] && seg[3] === 'toggle') {
      var cid = Number(seg[2]), cdate = q.get('date') || todayStr();
      var k = key(cid, cdate);
      M.done[k] = !M.done[k];
      if (!M.done[k]) delete M.done[k];
      return { id: cid, date: cdate, done: !!M.done[k] };
    }

    if (seg[1] === 'chores' && seg[2] && method === 'PATCH') {
      var mc = M.chores.filter(function (c) { return String(c.id) === seg[2]; })[0];
      if (!mc) { var e405 = new Error('Chore not found'); e405.status = 404; throw e405; }
      ['title', 'person_id', 'days', 'slot', 'stars'].forEach(function (f) {
        if (body[f] !== undefined) mc[f] = body[f];
      });
      return mc;
    }

    if (seg[1] === 'chores' && seg[2] && method === 'DELETE') {
      M.chores = M.chores.filter(function (c) { return String(c.id) !== seg[2]; });
      return { ok: true };
    }

    if (path === '/api/stars') {
      return {
        people: people().map(function (p) {
          var earnedTotal = 0;
          Object.keys(M.done).forEach(function (kk) {
            var cid = Number(kk.split('|')[0]);
            var c = M.chores.filter(function (x) { return x.id === cid; })[0];
            if (c && c.person_id === p.id) earnedTotal += c.stars || 1;
          });
          var wk = mockChoresFor(todayStr()).filter(function (x) { return x.id === p.id; })[0];
          var red = M.redeemed[p.id] || 0;
          return {
            id: p.id, earned_week: (wk && wk.stars_week) || 0, earned_total: earnedTotal + 24,
            redeemed_total: red, balance: earnedTotal + 24 - red
          };
        })
      };
    }

    if (path === '/api/stars/redeem') {
      M.redeemed[body.person_id] = (M.redeemed[body.person_id] || 0) + body.stars;
      var st = mockRoute('GET', '/api/stars');
      var row = st.people.filter(function (p) { return p.id === body.person_id; })[0];
      return { ok: true, balance: row ? row.balance : 0 };
    }

    /* ---- lists ---- */
    if (path === '/api/lists' && method === 'GET') return { lists: M.lists };

    if (path === '/api/lists' && method === 'POST') {
      var nl = { id: M.nextListId++, name: body.name, kind: body.kind || 'custom', items: [] };
      M.lists.push(nl);
      return nl;
    }

    if (seg[1] === 'lists' && seg[2] && seg[3] === 'items' && method === 'POST') {
      var lst = M.lists.filter(function (l) { return String(l.id) === seg[2]; })[0];
      var it = { id: M.nextItemId++, text: body.text, done: 0, created_at: new Date().toISOString() };
      if (lst) lst.items.push(it);
      return it;
    }

    if (seg[1] === 'lists' && seg[2] && method === 'DELETE') {
      M.lists = M.lists.filter(function (l) { return String(l.id) !== seg[2]; });
      return { ok: true };
    }

    if (seg[1] === 'items' && seg[2]) {
      var found = null, owner = null;
      M.lists.forEach(function (l) {
        l.items.forEach(function (x) { if (String(x.id) === seg[2]) { found = x; owner = l; } });
      });
      if (!found) { var e = new Error('Not found'); e.status = 404; throw e; }
      if (method === 'DELETE') {
        owner.items = owner.items.filter(function (x) { return x !== found; });
        return { ok: true };
      }
      if (body.done !== undefined) found.done = body.done ? 1 : 0;
      if (body.text !== undefined) found.text = body.text;
      return found;
    }

    /* ---- recipes + meal plan ---- */
    if (path === '/api/recipes' && method === 'GET') return { recipes: M.recipes };

    if (path === '/api/recipes' && method === 'POST') {
      var nr = {
        id: M.nextRecipeId++, name: body.name, notes: body.notes || null,
        ingredients: body.ingredients || [], source_url: null, meta: {}
      };
      M.recipes.push(nr);
      return nr;
    }

    /* The importer: same shape and same 422 wording style as recipes.py. */
    if (path === '/api/recipes/import' && method === 'POST') {
      var iurl = String((body && body.url) || '').trim();
      if (!/^https?:\/\//i.test(iurl)) {
        var eu = new Error("That doesn't look like a web address");
        eu.status = 422;
        throw eu;
      }
      if (/nolink|example\.com/i.test(iurl)) {
        var enf = new Error('No recipe found on that page — it may be an index or a video page');
        enf.status = 422;
        throw enf;
      }
      var slug = (iurl.split('?')[0].replace(/\/+$/, '').split('/').pop() || 'imported-recipe');
      var title = slug.replace(/[-_]+/g, ' ').replace(/\b\w/g, function (ch) { return ch.toUpperCase(); });
      var mr = {
        id: M.nextRecipeId++,
        name: title,
        notes: 'Imported from the web — ingredients, times and the method, credited to the site.',
        ingredients: [
          '1 kg chicken thigh fillets', '2 tbsp olive oil', '1 brown onion, finely diced',
          '3 garlic cloves, minced', '400g tin crushed tomatoes', '1 cup chicken stock',
          '1 tsp smoked paprika', '1/2 cup thickened cream', 'Salt and pepper', 'Parsley, to serve'
        ],
        source_url: iurl,
        meta: {
          servings: '4-5', prep_minutes: 15, cook_minutes: 30, total_minutes: 45,
          /* the backend caches the page's photo on import, so the demo does too */
          image_cached: true, image_data: PHOTO.tikka(), author: 'Nagi',
          /* and the page's own method comes across with it */
          steps: [
            'Season the chicken and brown it in a hot pan in two batches; set it aside.',
            'Soften the onion in the same pan, then add the garlic and paprika.',
            'Stir in the tomatoes and stock, scraping the base of the pan.',
            'Return the chicken and simmer 20 minutes until the sauce reduces.',
            'Stir the cream through and season to taste.',
            'Scatter with parsley and serve with rice or crusty bread.'
          ]
        }
      };
      M.recipes.push(mr);
      return mr;
    }

    /* Mirrors the backend: only name/notes/ingredients move, so an imported
       recipe keeps its source_url and meta through an edit. */
    if (seg[1] === 'recipes' && seg[2] && method === 'PATCH') {
      var mr2 = M.recipes.filter(function (r) { return String(r.id) === seg[2]; })[0];
      if (!mr2) { var e406 = new Error('Recipe not found'); e406.status = 404; throw e406; }
      if (body.name !== undefined) mr2.name = String(body.name).trim() || mr2.name;
      if (body.notes !== undefined) mr2.notes = body.notes;
      if (body.ingredients !== undefined) {
        mr2.ingredients = (body.ingredients || []).map(function (x) { return String(x).trim(); })
          .filter(function (x) { return x; });
      }
      // planned meals carry a denormalised name, exactly as the backend does
      Object.keys(M.plan).forEach(function (dt) {
        ['dinner', 'lunch', 'other'].forEach(function (sl) {
          var row = M.plan[dt][sl];
          if (row && row.recipe_id === mr2.id) row.recipe_name = mr2.name;
        });
      });
      return mr2;
    }

    if (seg[1] === 'recipes' && seg[2] && method === 'DELETE') {
      M.recipes = M.recipes.filter(function (r) { return String(r.id) !== seg[2]; });
      return { ok: true };
    }

    if (path === '/api/mealplan' && method === 'GET') {
      var ws = mondayOf(q.get('week') || todayStr());
      var days = [];
      for (var j = 0; j < 7; j++) {
        var dd = addDays(ws, j);
        var row = M.plan[dd] || {};
        days.push({ date: dd, dinner: row.dinner || null, lunch: row.lunch || null, other: row.other || null });
      }
      return { week_start: ws, days: days };
    }

    if (path === '/api/mealplan' && method === 'PUT') {
      var row2 = M.plan[body.date] || (M.plan[body.date] = { dinner: null, lunch: null, other: null });
      if (body.recipe_id == null && !body.free_text) row2[body.slot] = null;
      else row2[body.slot] = {
        recipe_id: body.recipe_id || null,
        recipe_name: body.recipe_id ? recipeName(M.recipes, body.recipe_id) : null,
        free_text: body.free_text || null
      };
      return row2[body.slot] || { recipe_id: null, recipe_name: null, free_text: null };
    }

    if (path === '/api/mealplan/push_groceries') {
      var pr = (M.plan[body.date] || {})[body.slot];
      if (!pr || !pr.recipe_id) return { added: [], skipped: [] };
      var rec = M.recipes.filter(function (r) { return r.id === pr.recipe_id; })[0];
      var groc = M.lists.filter(function (l) { return l.kind === 'grocery'; })[0] || M.lists[0];
      var added = [], skipped = [];
      (rec.ingredients || []).forEach(function (ing) {
        var dupe = groc.items.some(function (x) {
          return !x.done && x.text.toLowerCase() === ing.toLowerCase();
        });
        if (dupe) skipped.push(ing);
        else { groc.items.push({ id: M.nextItemId++, text: ing, done: 0, created_at: new Date().toISOString() }); added.push(ing); }
      });
      return { added: added, skipped: skipped };
    }

    /* ---- magic import ---- */
    if (path === '/api/imports/pending') return { imports: M.imports };

    if (seg[1] === 'imports' && seg[2] && seg[3] === 'approve') {
      var imp = M.imports.filter(function (x) { return String(x.id) === seg[2]; })[0];
      var created = [];
      if (imp) {
        (body.events || []).forEach(function (sel) {
          var src = imp.events.filter(function (x) { return x.index === sel.index; })[0];
          if (!src) return;
          var ce = {
            id: M.nextId++, source: 'local', title: sel.title || src.title,
            person_id: sel.person_id || 'family', location: src.location || null,
            all_day: sel.all_day !== undefined ? sel.all_day : !!src.all_day,
            date: sel.date || src.date, end_date: sel.date || src.date,
            start: src.all_day ? null : (sel.start_time || src.start_time),
            end: src.all_day ? null : (sel.end_time || src.end_time),
            start_utc: null, end_utc: null, editable: true, notes: src.notes || null, countdown: false
          };
          M.events.push(ce);
          created.push(ce);
        });
        M.imports = M.imports.filter(function (x) { return x !== imp; });
      }
      return { ok: true, created: created };
    }

    if (seg[1] === 'imports' && seg[2] && seg[3] === 'dismiss') {
      M.imports = M.imports.filter(function (x) { return String(x.id) !== seg[2]; });
      return { ok: true };
    }

    /* ---- home assistant ---- */
    if (path === '/api/ha/tiles') {
      // gentle drift so the panel looks alive
      var temp = M.tiles.filter(function (t) { return t.entity === 'sensor.outside_temp'; })[0];
      if (temp) {
        temp.attrs.value = Math.round((25.8 + Math.sin(Date.now() / 900000) * 1.6) * 10) / 10;
        temp.state = String(temp.attrs.value);
      }
      mockSolarDrift();
      return { available: true, updated_at: new Date().toISOString(), tiles: M.tiles };
    }

    if (path === '/api/ha/action') {
      var tile = M.tiles.filter(function (t) { return t.entity === body.entity; })[0];
      if (!tile) { var e2 = new Error('Unknown entity'); e2.status = 404; throw e2; }
      if (!tile.allow_action) { var e3 = new Error('That tile is read-only'); e3.status = 403; throw e3; }
      if (body.action === 'open') tile.state = 'open';
      else if (body.action === 'close') tile.state = 'closed';
      /* a Sensibo comes back in the mode it was switched off in, and its
         state and hvac_mode move together — exactly as HA reports them */
      else if (body.action === 'turn_on') {
        if (tile.type === 'climate') {
          tile.attrs.hvac_mode = tile.attrs.last_mode || 'cool';
          tile.state = tile.attrs.hvac_mode;
        } else {
          tile.state = 'on';
          if (tile.type === 'fan') tile.attrs.on = true;
        }
      }
      else if (body.action === 'turn_off') {
        if (tile.type === 'climate') {
          if (tile.attrs.hvac_mode && tile.attrs.hvac_mode !== 'off') tile.attrs.last_mode = tile.attrs.hvac_mode;
          tile.attrs.hvac_mode = 'off';
        }
        tile.state = 'off';
        if (tile.type === 'fan') tile.attrs.on = false;
      }
      else if (body.action === 'set_temperature') tile.attrs.setpoint = Number(body.value);
      /* the Dyson: speed steps switch it on, oscillation is independent */
      else if (body.action === 'set_percentage') {
        tile.attrs.percentage = Math.max(0, Math.min(100, Number(body.value) || 0));
        tile.attrs.on = tile.attrs.percentage > 0;
        tile.state = tile.attrs.on ? 'on' : 'off';
      }
      else if (body.action === 'oscillate_on') tile.attrs.oscillating = true;
      else if (body.action === 'oscillate_off') tile.attrs.oscillating = false;
      /* the favourites list: picking one starts that station or playlist */
      else if (body.action === 'select_source') {
        var src = String(body.value == null ? '' : body.value);
        var now = SOURCE_NOW[src] || { title: src, artist: 'Sonos favourite' };
        tile.attrs.source = src;
        tile.attrs.title = now.title;
        tile.attrs.artist = now.artist;
        tile.attrs.playing = true;
        tile.state = 'playing';
      }
      else if (body.action === 'next' || body.action === 'previous') {
        var dir = body.action === 'next' ? 1 : -1;
        M.trackIx = (M.trackIx + dir + TRACKS.length) % TRACKS.length;
        tile.attrs.title = TRACKS[M.trackIx].title;
        tile.attrs.artist = TRACKS[M.trackIx].artist;
        tile.attrs.playing = true;
        tile.state = 'playing';
      }
      /* The demo Sonos works its way down a short playlist, so pressing play
         in the now-playing overlay actually changes what is on. */
      else if (body.action === 'play') {
        tile.attrs.playing = true;
        tile.state = 'playing';
        if (tile.type === 'media') {
          M.trackIx = (M.trackIx + 1) % TRACKS.length;
          tile.attrs.title = TRACKS[M.trackIx].title;
          tile.attrs.artist = TRACKS[M.trackIx].artist;
        }
      }
      else if (body.action === 'pause') { tile.attrs.playing = false; tile.state = 'paused'; }
      else if (body.action === 'volume_set') {
        tile.attrs.volume = Math.max(0, Math.min(1, Number(body.value) || 0));
      }
      return { ok: true };
    }

    /* ---- assist ---- */
    /* Canned, but wired to the same demo data everything else reads, so
       "what's on today" answers with the agenda actually on screen and
       "turn on the porch light" really does flip the tile behind the sheet. */
    if (path === '/api/assist/text') {
      var said = String((body && body.text) || '').trim();
      if (!said) { var e400 = new Error('Nothing to ask.'); e400.status = 400; throw e400; }
      return { ok: true, response_text: mockAssistAnswer(said) };
    }

    if (path === '/api/assist/audio') {
      var heard = MOCK_HEARD[M.assistIx % MOCK_HEARD.length];
      M.assistIx++;
      return { ok: true, heard: heard, response_text: mockAssistAnswer(heard), tts_path: null };
    }

    /* ---- photos (the screensaver's manifest) ---- */
    /* Same shape as the live route, with one demo-only extra: `src` carries
       the drawn scene, so the frame never asks for /api/photos/<id>/image. */
    if (path === '/api/photos') {
      return {
        available: true,
        updated_at: new Date(Date.now() - 3600000).toISOString(),
        interval_s: 12,
        idle_minutes: 3,
        photos: DEMO_PHOTOS.map(function (src, i) {
          return {
            id: i + 1, src: src,
            added_at: new Date(Date.now() - (i + 1) * 86400000).toISOString()
          };
        })
      };
    }

    /* ---- weather ---- */
    if (path === '/api/weather') return M.weather;

    var err404 = new Error('No mock for ' + method + ' ' + path);
    err404.status = 404;
    throw err404;
  }

  /* Expose the mock so integration tests can drive it. Inert without ?mock=1. */
  window.__mock = {
    enabled: MOCK,
    init: mockInit,
    route: function (method, url, body) { if (!M) mockInit(); return mockRoute(method, url, body); },
    data: function () { return M; }
  };

  /* ------------------------------------------------------------------ go -- */

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
