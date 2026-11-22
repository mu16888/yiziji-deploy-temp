/* Shared helpers for the product pages. CSRF is ALWAYS read from the cookie:
   the login page's old meta token went stale after register/login rotated it. */
(function () {
  function getCookie(name) {
    const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return match ? decodeURIComponent(match[1]) : '';
  }

  function csrfToken() {
    return getCookie('csrf_token');
  }

  async function apiFetch(path, options) {
    const opts = Object.assign({}, options || {});
    const headers = Object.assign({}, opts.headers || {});
    if (typeof opts.body === 'string') headers['Content-Type'] = 'application/json';
    // Cookie-authenticated writes need the double-submit token from the cookie.
    headers['X-CSRF-Token'] = csrfToken();
    opts.headers = headers;
    const response = await fetch(path, opts);
    let data = null;
    try { data = await response.json(); } catch (e) { /* non-JSON response */ }
    return { ok: response.ok, status: response.status, data: data };
  }

  window.getCookie = getCookie;
  window.csrfToken = csrfToken;
  window.apiFetch = apiFetch;
})();
