/*
 * ShariahEase Settings page: download your expenses, delete your account.
 *
 * UR: every Urdu string in the dictionary below awaits native-speaker review before launch (see the release checklist).
 *
 * - Needs: authHeaders() from base.html (login token). The page never builds a user id: the server takes it from the session.
 * - Export:  GET /api/expenses/export?format=json|csv  (the browser saves the file; nothing is cached)
 * - Delete:  DELETE /auth/me with the password again; a fresh token is requested first because /auth/ calls are not
 *            refreshed by the page-wide fetch wrapper and the access token lives only 15 minutes.
 */
(function () {
  const TEXT = {
    en: {
      title: 'Settings', login_needed: 'Log in to see your settings.', login: 'Log in',
      data_h: 'Your data',
      data_p: 'Download every expense you still have, notes included. Deleted entries that are still inside their 30-day undo period are included too (they have a deleted_at date).',
      dl_json: 'Download JSON', dl_csv: 'Download CSV (Excel)',
      dl_busy: 'Preparing your file...', dl_done: 'Your file was saved.',
      err_dl: 'The download did not work. Please try again.',
      err_dl_rate: 'Too many downloads. Try again in {n} seconds.',
      err_session: 'Your session has ended. Please log in again.',
      privacy_h: 'Privacy', privacy_p: 'Read what we keep, who else sees it, and what deleting your account does.', privacy_link: 'Open the privacy page (English)',
      del_h: 'Delete my account',
      del_p1: 'This is permanent. Your account and everything linked to it (expenses, activity log, Zakat history, screener history, chat history) is deleted at once and cannot be brought back.',
      del_p2: 'Download your data first if you want to keep it.',
      pw: 'Type your password again', understand: 'I understand this cannot be undone',
      del_btn: 'Delete my account and all my data', del_busy: 'Deleting...',
      deleted: 'Your account has been deleted. Taking you to the home page...',
      err_pw: 'That password is not right.',
      err_del_rate: 'Too many attempts. Try again in {n} seconds.',
      err_del: 'The account could not be deleted. Please try again.',
    },
    ur: {
      title: 'ترتیبات', login_needed: 'ترتیبات دیکھنے کے لیے لاگ اِن کریں۔', login: 'لاگ اِن',
      data_h: 'میرا ڈیٹا',
      data_p: 'اپنے سارے خرچ ڈاؤن لوڈ کریں، نوٹ سمیت۔ حذف کیے ہوئے وہ اندراجات بھی شامل ہیں جو ابھی 30 دن کی واپسی کی مدت میں ہیں (ان پر حذف کی تاریخ لکھی ہوگی)۔',
      dl_json: 'JSON ڈاؤن لوڈ کریں', dl_csv: 'CSV ڈاؤن لوڈ کریں (ایکسل)',
      dl_busy: 'آپ کی فائل تیار ہو رہی ہے...', dl_done: 'آپ کی فائل محفوظ ہو گئی۔',
      err_dl: 'ڈاؤن لوڈ نہیں ہو سکا۔ براہِ کرم دوبارہ کوشش کریں۔',
      err_dl_rate: 'بہت زیادہ ڈاؤن لوڈ۔ {n} سیکنڈ بعد دوبارہ کوشش کریں۔',
      err_session: 'آپ کا سیشن ختم ہو گیا ہے۔ براہِ کرم دوبارہ لاگ اِن کریں۔',
      privacy_h: 'رازداری', privacy_p: 'پڑھیں کہ ہم کیا محفوظ رکھتے ہیں، اور کس کو کیا نظر آتا ہے، اور اکاؤنٹ حذف کرنے سے کیا ہوتا ہے۔', privacy_link: 'رازداری کا صفحہ کھولیں (انگریزی میں)',
      del_h: 'میرا اکاؤنٹ حذف کریں',
      del_p1: 'یہ ہمیشہ کے لیے ہے۔ آپ کا اکاؤنٹ اور اس سے جڑا سارا ڈیٹا (خرچ، سرگرمی کا ریکارڈ، زکوٰۃ کی تاریخ، اسکرینر کی تاریخ، چیٹ کی تاریخ) فوراً حذف ہو جائے گا اور واپس نہیں آ سکے گا۔',
      del_p2: 'اگر ڈیٹا رکھنا ہو تو پہلے اسے ڈاؤن لوڈ کر لیں۔',
      pw: 'اپنا پاس ورڈ دوبارہ لکھیں', understand: 'میں سمجھتا/سمجھتی ہوں کہ یہ واپس نہیں ہو سکتا',
      del_btn: 'میرا اکاؤنٹ اور سارا ڈیٹا حذف کریں', del_busy: 'حذف ہو رہا ہے...',
      deleted: 'آپ کا اکاؤنٹ حذف ہو گیا ہے۔ ہوم پیج پر لے جایا جا رہا ہے...',
      err_pw: 'پاس ورڈ درست نہیں ہے۔',
      err_del_rate: 'بہت زیادہ کوششیں۔ {n} سیکنڈ بعد دوبارہ کوشش کریں۔',
      err_del: 'اکاؤنٹ حذف نہیں ہو سکا۔ براہِ کرم دوبارہ کوشش کریں۔',
    },
  };

  const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };    // same anti-forgery header as the expense writes
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: fine */ } },
    remove(k) { try { localStorage.removeItem(k); } catch (e) { /* ignore */ } },
  };

  function retryAfter(res) {
    const n = parseInt(res.headers.get('Retry-After') || '', 10);
    return Number.isFinite(n) && n > 0 ? n : 60;
  }

  window.settingsApp = function settingsApp() {
    return {
      lang: store.get('se_lang') === 'ur' ? 'ur' : 'en',
      authed: !!store.get('se_token'),
      busy: '', dlMsg: '', dlErr: '', dlLoginAgain: false,
      password: '', understood: false, deleting: false, delErr: '', deleted: false,

      get dir() { return this.lang === 'ur' ? 'rtl' : 'ltr'; },
      get canDelete() { return this.password.length > 0 && this.understood && !this.deleting && !this.deleted; },
      t(key, vars) {
        let s = (TEXT[this.lang] && TEXT[this.lang][key]) || TEXT.en[key] || key;
        if (vars) Object.keys(vars).forEach((k) => { s = s.replace('{' + k + '}', vars[k]); });
        return s;
      },
      init() {
        document.documentElement.lang = this.lang === 'ur' ? 'ur' : 'en';
        this.$watch('lang', () => { this.dlMsg = ''; this.dlErr = ''; this.delErr = ''; });
      },
      setLang(l) { this.lang = l; store.set('se_lang', l); document.documentElement.lang = l === 'ur' ? 'ur' : 'en'; },

      // ── export ────────────────────────────────────────────────────────────────
      async download(fmt) {
        if (this.busy) return;
        this.busy = fmt; this.dlMsg = ''; this.dlErr = ''; this.dlLoginAgain = false;
        try {
          const res = await fetch('/api/expenses/export?format=' + encodeURIComponent(fmt), { headers: authHeaders() });
          if (res.status === 401) { this.dlErr = this.t('err_session'); this.dlLoginAgain = true; return; }
          if (res.status === 429) { this.dlErr = this.t('err_dl_rate', { n: retryAfter(res) }); return; }
          if (!res.ok) { this.dlErr = this.t('err_dl'); return; }
          const blob = await res.blob();
          const m = /filename="([^"]+)"/.exec(res.headers.get('Content-Disposition') || '');
          const name = m ? m[1] : 'shariahease-expenses.' + fmt;
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url; a.download = name; a.rel = 'noopener';
          document.body.appendChild(a); a.click(); a.remove();
          setTimeout(() => URL.revokeObjectURL(url), 2000);
          this.dlMsg = this.t('dl_done');
        } catch (e) {
          this.dlErr = this.t('err_dl');
        } finally {
          this.busy = '';
        }
      },

      // ── delete account ────────────────────────────────────────────────────────
      async freshToken() {
        try {
          const r = await fetch('/auth/refresh', { method: 'POST', credentials: 'same-origin' });
          if (r.ok) { const d = await r.json(); if (d && d.access_token) { store.set('se_token', d.access_token); return d.access_token; } }
        } catch (e) { /* fall back to the token we have */ }
        return store.get('se_token');
      },
      async deleteAccount() {
        if (!this.canDelete) return;
        this.deleting = true; this.delErr = '';
        try {
          const token = await this.freshToken();
          const headers = Object.assign({ 'Content-Type': 'application/json' }, WRITE_HEADER);
          if (token) headers['Authorization'] = 'Bearer ' + token;
          const res = await fetch('/auth/me', { method: 'DELETE', credentials: 'same-origin', headers, body: JSON.stringify({ password: this.password }) });
          if (res.ok) {
            this.password = ''; this.deleted = true;
            store.remove('se_token'); store.remove('se_user');          // the language choice stays
            setTimeout(() => { window.location.assign('/'); }, 2500);
            return;
          }
          if (res.status === 401) this.delErr = this.t('err_session');
          else if (res.status === 429) this.delErr = this.t('err_del_rate', { n: retryAfter(res) });
          else if (res.status === 403) this.delErr = this.t('err_pw');
          else this.delErr = this.t('err_del');
        } catch (e) {
          this.delErr = this.t('err_del');
        } finally {
          this.deleting = false;
        }
      },
    };
  };
})();
