/*
 * Expenses page (Alpine component). Talks only to /api/expenses/*.
 * Money: the server returns integer paisa; the form sends the rupees text exactly as typed and the server parses it.
 * The browser never adds up money for display: the month total and the category totals come from the server.
 *
 * URDU TEXT: every Urdu string below is marked UR and is awaiting review by a native speaker.
 */
(function () {
  'use strict';

  const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };
  const PAYMENT_METHODS = ['cash', 'bank_transfer', 'card', 'wallet', 'other'];
  const UNDO_MS = 8000;

  const TEXT = {
    en: {
      login_needed: 'Log in to use the expense tracker.', login: 'Log in',
      prev_month: 'Previous month', next_month: 'Next month',
      month_total: 'Spent this month', entries: 'entries',
      add: 'Add an expense', amount: 'Amount (Rs)', date: 'Date', payment: 'Paid by',
      category: 'Category', choose_category: 'Choose a category', general: 'general', note: 'Note (optional)',
      pm_cash: 'Cash', pm_bank_transfer: 'Bank transfer', pm_card: 'Card', pm_wallet: 'Wallet (JazzCash, Easypaisa)', pm_other: 'Other',
      save: 'Save', saving: 'Saving…', cancel: 'Cancel', edit: 'Edit expense', delete: 'Delete', undo: 'Undo',
      by_category: 'By category', this_month_list: 'Expenses', loading: 'Loading…', empty: 'No expenses in this month yet.',
      load_more: 'Show more', load_failed: 'Could not load your expenses.', retry: 'Try again',
      charity_badge: 'Zakat / Sadaqah', flag_riba: 'Possible riba', flag_doubtful: 'Doubtful',
      flag_note: 'These marks are only reminders, not a fatwa. Please ask a qualified scholar.',
      zakat_link: 'Open the Zakat calculator',
      saved: 'Expense saved', updated: 'Expense updated', deleted: 'Expense deleted', restored: 'Expense restored',
      err_network: 'No connection. Nothing was saved. Please try again.',
      err_server: 'Something went wrong. Nothing was saved. Please try again.',
      err_conflict: 'This expense was changed somewhere else. The latest version is shown.',
      err_gone: 'This expense no longer exists.',
      err_deleted_before: 'This expense was deleted earlier, so it was not added again.',
      err_rate: 'Too many changes. Try again in {n} seconds.',
      err_amount_empty: 'Enter an amount.', err_amount_format: 'Use digits only, like 1250 or 1,250.50.',
      err_amount_too_many_decimals: 'At most 2 digits after the point.', err_amount_not_positive: 'The amount must be more than zero.',
      err_amount_too_large: 'The amount is too large.',
      err_date: 'Check the date (at most 1 day ahead).', err_category: 'Choose a category.', err_note: 'The note is too long (500 letters at most).',
      err_generic: 'Please check this field.',
    },
    ur: {   // UR: all of this awaits native-speaker review
      login_needed: 'اخراجات کا حساب رکھنے کے لیے لاگ اِن کریں۔', login: 'لاگ اِن',
      prev_month: 'پچھلا مہینہ', next_month: 'اگلا مہینہ',
      month_total: 'اس مہینے کے اخراجات', entries: 'اندراجات',
      add: 'خرچ شامل کریں', amount: 'رقم (روپے)', date: 'تاریخ', payment: 'ادائیگی کا طریقہ',
      category: 'زمرہ', choose_category: 'زمرہ منتخب کریں', general: 'عمومی', note: 'نوٹ (اختیاری)',
      pm_cash: 'نقد', pm_bank_transfer: 'بینک ٹرانسفر', pm_card: 'کارڈ', pm_wallet: 'والٹ (جاز کیش، ایزی پیسہ)', pm_other: 'دیگر',
      save: 'محفوظ کریں', saving: 'محفوظ ہو رہا ہے…', cancel: 'منسوخ کریں', edit: 'خرچ میں ترمیم', delete: 'حذف کریں', undo: 'واپس لائیں',
      by_category: 'زمرے کے لحاظ سے', this_month_list: 'اخراجات', loading: 'لوڈ ہو رہا ہے…', empty: 'اس مہینے میں ابھی کوئی خرچ نہیں۔',
      load_more: 'مزید دکھائیں', load_failed: 'آپ کے اخراجات لوڈ نہیں ہو سکے۔', retry: 'دوبارہ کوشش کریں',
      charity_badge: 'زکوٰۃ / صدقہ', flag_riba: 'ممکنہ سود', flag_doubtful: 'مشکوک',
      flag_note: 'یہ نشان صرف یاد دہانی ہیں، فتویٰ نہیں۔ براہِ کرم کسی مستند عالمِ دین سے پوچھیں۔',
      zakat_link: 'زکوٰۃ کیلکولیٹر کھولیں',
      saved: 'خرچ محفوظ ہو گیا', updated: 'خرچ تبدیل ہو گیا', deleted: 'خرچ حذف ہو گیا', restored: 'خرچ بحال ہو گیا',
      err_network: 'انٹرنیٹ نہیں ہے۔ کچھ محفوظ نہیں ہوا۔ دوبارہ کوشش کریں۔',
      err_server: 'کچھ خرابی ہو گئی۔ کچھ محفوظ نہیں ہوا۔ دوبارہ کوشش کریں۔',
      err_conflict: 'یہ خرچ کہیں اور تبدیل ہو چکا ہے۔ تازہ ترین صورت دکھائی گئی ہے۔',
      err_gone: 'یہ خرچ اب موجود نہیں۔',
      err_deleted_before: 'یہ خرچ پہلے حذف ہو چکا تھا، اس لیے دوبارہ شامل نہیں کیا گیا۔',
      err_rate: 'بہت زیادہ تبدیلیاں۔ {n} سیکنڈ بعد دوبارہ کوشش کریں۔',
      err_amount_empty: 'رقم لکھیں۔', err_amount_format: 'صرف ہندسے لکھیں، جیسے 1250 یا 1,250.50۔',
      err_amount_too_many_decimals: 'اعشاریہ کے بعد زیادہ سے زیادہ 2 ہندسے۔', err_amount_not_positive: 'رقم صفر سے زیادہ ہونی چاہیے۔',
      err_amount_too_large: 'رقم بہت زیادہ ہے۔',
      err_date: 'تاریخ دیکھیں (زیادہ سے زیادہ ایک دن آگے)۔', err_category: 'زمرہ منتخب کریں۔', err_note: 'نوٹ بہت لمبا ہے (زیادہ سے زیادہ 500 حروف)۔',
      err_generic: 'براہِ کرم اس خانے کو دیکھیں۔',
    },
  };

  const pad = (n) => String(n).padStart(2, '0');
  const ymd = (d) => d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  const newId = () => (window.crypto && crypto.randomUUID)
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (crypto.getRandomValues(new Uint8Array(1))[0] & 15);
        return (c === 'x' ? r : (r & 3) | 8).toString(16);
      });
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: fine */ } },
  };

  // integer paisa -> "1,250.50" (no floats: whole rupees and the remainder are taken separately)
  function rupees(p) {
    const cents = p % 100, whole = (p - cents) / 100;
    return whole.toLocaleString('en-US') + (cents ? '.' + pad(cents) : '');
  }
  function rupeesPlain(p) {           // for the edit form: "1250" or "1250.50"
    const cents = p % 100, whole = (p - cents) / 100;
    return String(whole) + (cents ? '.' + pad(cents) : '');
  }

  window.expenseApp = function expenseApp() {
    let chartRef = null;                 // kept outside Alpine's reactive data (Chart.js does not like being proxied)
    const today = new Date();
    const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
    return {
      lang: store.get('se_lang') === 'ur' ? 'ur' : 'en',
      authed: !!store.get('se_token'),
      paymentMethods: PAYMENT_METHODS,
      categories: [], catMap: {}, subMap: {},
      thisMonth: ymd(today).slice(0, 7), month: ymd(today).slice(0, 7), maxDate: ymd(tomorrow),
      items: [], nextBefore: null, summary: null, chartGroups: [], chartHeight: 120,
      loading: false, loadingMore: false, loadError: false, saving: false, seq: 0,
      form: null, formErrors: {}, formMessage: '',
      editing: null, edit: { amount: '', occurred_on: '', cat: '', payment_method: 'cash', note: '' }, editErrors: {}, editMessage: '',
      toast: { text: '', undoId: null }, toastTimer: null,

      get dir() { return this.lang === 'ur' ? 'rtl' : 'ltr'; },
      t(key, vars) {
        let s = (TEXT[this.lang] && TEXT[this.lang][key]) || TEXT.en[key] || key;
        if (vars) Object.keys(vars).forEach((k) => { s = s.replace('{' + k + '}', vars[k]); });
        return s;
      },
      fmt(p) { return 'Rs ' + rupees(p); },
      catName(o) { return this.lang === 'ur' ? o.name_ur : o.name_en; },

      async init() {
        this.resetForm();
        if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
        if (!this.authed) return;
        this.$watch('lang', () => {            // messages already shown are in the old language: drop them
          this.formErrors = {}; this.formMessage = ''; this.editErrors = {}; this.editMessage = '';
          this.renderChart();
        });
        await this.loadCategories();
        await this.reload();
      },

      setLang(l) { this.lang = l; store.set('se_lang', l); document.documentElement.lang = l === 'ur' ? 'ur' : 'en'; },

      // ── api ──────────────────────────────────────────────────────────────────
      async api(method, url, body) {
        const headers = Object.assign({}, authHeaders(), method === 'GET' ? {} : WRITE_HEADER);
        let res;
        try {
          res = await fetch(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
        } catch (e) {
          return { ok: false, status: 0, data: null };
        }
        if (handleAuthError(res)) return { ok: false, status: 401, data: null };
        let data = null;
        try { data = await res.json(); } catch (e) { /* no body */ }
        return { ok: res.ok, status: res.status, data, retryAfter: res.headers.get('Retry-After') };
      },

      async loadCategories() {
        const r = await this.api('GET', '/api/expenses/categories');
        if (!r.ok) return;
        this.categories = r.data;
        this.catMap = {}; this.subMap = {};
        r.data.forEach((c) => { this.catMap[c.slug] = c; c.subcategories.forEach((s) => { this.subMap[s.slug] = s; }); });
      },

      async reload() {
        const mine = ++this.seq;
        this.loading = true; this.loadError = false;
        const [list, sum] = await Promise.all([
          this.api('GET', '/api/expenses?month=' + this.month + '&limit=50'),
          this.api('GET', '/api/expenses/summary?month=' + this.month + '&group_by=category'),
        ]);
        if (mine !== this.seq) return;                       // the month changed while loading: ignore this answer
        this.loading = false;
        if (!list.ok || !sum.ok) { this.loadError = this.authed && list.status !== 401; return; }
        this.items = list.data.items; this.nextBefore = list.data.next_before;
        this.summary = sum.data;
        this.buildChart();
      },

      async loadMore() {
        if (!this.nextBefore || this.loadingMore) return;
        const mine = this.seq;
        this.loadingMore = true;
        const nb = this.nextBefore;
        const r = await this.api('GET', '/api/expenses?month=' + this.month + '&limit=50&before_date=' + nb.date + '&before_id=' + nb.id);
        this.loadingMore = false;
        if (mine !== this.seq || !r.ok) return;
        this.items = this.items.concat(r.data.items); this.nextBefore = r.data.next_before;
      },

      shiftMonth(delta) {
        let [y, m] = this.month.split('-').map(Number);
        m += delta;
        if (m < 1) { m = 12; y -= 1; } else if (m > 12) { m = 1; y += 1; }
        const next = y + '-' + pad(m);
        if (next > this.thisMonth || y < 2000) return;
        this.month = next; this.items = []; this.summary = null; this.nextBefore = null;
        this.reload();
      },
      monthLabel() {
        const [y, m] = this.month.split('-').map(Number);
        return new Intl.DateTimeFormat(this.lang === 'ur' ? 'ur-PK' : 'en-GB', { month: 'long', year: 'numeric' })
          .format(new Date(y, m - 1, 1));
      },
      dayLabel(iso) {
        const [y, m, d] = iso.split('-').map(Number);
        return new Intl.DateTimeFormat(this.lang === 'ur' ? 'ur-PK' : 'en-GB', { weekday: 'short', day: 'numeric', month: 'short' })
          .format(new Date(y, m - 1, d));
      },

      // ── list helpers ─────────────────────────────────────────────────────────
      get days() {
        const out = [], idx = {};
        this.items.forEach((x) => {
          if (!(x.occurred_on in idx)) { idx[x.occurred_on] = out.length; out.push({ date: x.occurred_on, items: [] }); }
          out[idx[x.occurred_on]].items.push(x);
        });
        return out;
      },
      rowTitle(x) {
        const s = x.subcategory_slug && this.subMap[x.subcategory_slug];
        const c = this.catMap[x.category_slug];
        return s ? this.catName(s) : (c ? this.catName(c) : x.category_slug);
      },
      isCharity(x) { const c = this.catMap[x.category_slug]; return !!(c && c.is_charity); },

      // ── chart: ranked horizontal bars, one hue; the list under it is the text version ──
      buildChart() {
        const groups = (this.summary ? this.summary.groups : []).slice().sort((a, b) => b.total_paisa - a.total_paisa);
        this.chartGroups = groups.map((g) => ({ key: g.key, total_paisa: g.total_paisa, en: g.label_en || g.key, ur: g.label_ur || g.label_en || g.key }));
        this.chartHeight = Math.max(this.chartGroups.length, 1) * 30 + 16;
        this.$nextTick(() => this.renderChart());
      },
      renderChart() {
        const canvas = document.getElementById('cat-chart');
        if (chartRef) { chartRef.destroy(); chartRef = null; }
        if (!canvas || !window.Chart || !this.chartGroups.length) return;
        const rows = this.chartGroups.map((g) => ({ label: this.lang === 'ur' ? g.ur : g.en, value: g.total_paisa }));
        const calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
        const self = this;
        chartRef = new Chart(canvas, {
          type: 'bar',
          data: { labels: rows.map((r) => r.label), datasets: [{ data: rows.map((r) => r.value), backgroundColor: '#4edea3', borderRadius: 4, barThickness: 14 }] },
          options: {
            indexAxis: 'y', responsive: true, maintainAspectRatio: false, animation: calm ? false : { duration: 300 },
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: (c) => self.fmt(c.raw) } } },
            scales: {
              x: { display: false, beginAtZero: true },
              y: { grid: { display: false }, border: { display: false }, ticks: { color: '#c3c2b7', font: { size: 12 } } },
            },
          },
        });
      },

      // ── add ──────────────────────────────────────────────────────────────────
      resetForm() {
        this.form = { client_id: newId(), amount: '', occurred_on: ymd(new Date()), cat: '', payment_method: 'cash', note: '' };
        this.formErrors = {}; this.formMessage = '';
      },

      payload(f) {
        const body = { amount: String(f.amount).trim(), occurred_on: f.occurred_on, payment_method: f.payment_method, note: f.note.trim() || null };
        if (f.cat.startsWith('s:')) body.subcategory_slug = f.cat.slice(2);
        else if (f.cat.startsWith('c:')) body.category_slug = f.cat.slice(2);
        return body;
      },

      // turns the server's error answer into messages next to the fields; returns a general message otherwise
      explain(r, errors) {
        Object.keys(errors).forEach((k) => delete errors[k]);
        if (r.status === 0) return this.t('err_network');
        if (r.status === 429) return this.t('err_rate', { n: r.retryAfter || 60 });
        if (r.status === 409 && r.data && r.data.detail && r.data.detail.code === 'already_deleted') return this.t('err_deleted_before');
        if (r.status === 422 && r.data && r.data.detail) {
          const list = Array.isArray(r.data.detail.errors) ? r.data.detail.errors
            : Array.isArray(r.data.detail) ? r.data.detail.map((d) => ({ field: String((d.loc || []).slice(-1)[0] || ''), problem: '' })) : [];
          let any = false;
          list.forEach((e) => {
            const f = e.field || '';
            if (f === 'amount' || f === 'amount_paisa') { errors.amount = this.t('err_amount_' + e.problem) !== 'err_amount_' + e.problem ? this.t('err_amount_' + e.problem) : this.t('err_amount_format'); any = true; }
            else if (f.indexOf('occurred_on') === 0) { errors.occurred_on = this.t('err_date'); any = true; }
            else if (f.indexOf('category') >= 0 || f.indexOf('subcategory') >= 0) { errors.category = this.t('err_category'); any = true; }
            else if (f === 'note') { errors.note = this.t('err_note'); any = true; }
          });
          if (!any) return this.t('err_generic');
          return '';
        }
        return this.t('err_server');
      },

      async submitAdd() {
        if (this.saving) return;
        const f = this.form;
        this.formErrors = {}; this.formMessage = '';
        if (!String(f.amount).trim()) { this.formErrors = { amount: this.t('err_amount_empty') }; return; }
        if (!f.cat) { this.formErrors = { category: this.t('err_category') }; return; }
        this.saving = true;
        const r = await this.api('POST', '/api/expenses', Object.assign({ client_id: f.client_id }, this.payload(f)));
        this.saving = false;
        if (!r.ok) { this.formMessage = this.explain(r, this.formErrors); return; }   // client_id is kept, so a retry is safe
        const keepDate = f.occurred_on, keepCat = f.cat, keepPay = f.payment_method;
        this.resetForm();
        this.form.occurred_on = keepDate; this.form.cat = keepCat; this.form.payment_method = keepPay;   // quick repeat entry
        this.say(this.t('saved'));
        if (keepDate.slice(0, 7) !== this.month) { this.month = keepDate.slice(0, 7); }
        await this.reload();
      },

      // ── edit ─────────────────────────────────────────────────────────────────
      openEdit(x) {
        this.editing = x;
        this.edit = {
          amount: rupeesPlain(x.amount_paisa), occurred_on: x.occurred_on, payment_method: x.payment_method,
          cat: x.subcategory_slug ? 's:' + x.subcategory_slug : 'c:' + x.category_slug, note: x.note || '',
        };
        this.editErrors = {}; this.editMessage = '';
        this.clearToast();
        this.$nextTick(() => { const el = document.getElementById('edit-amount'); if (el) el.focus(); });
      },
      closeEdit() { this.editing = null; },

      async submitEdit() {
        if (this.saving || !this.editing) return;
        const x = this.editing, f = this.edit, body = { version: x.version };
        this.editErrors = {}; this.editMessage = '';
        if (!String(f.amount).trim()) { this.editErrors = { amount: this.t('err_amount_empty') }; return; }
        if (!f.cat) { this.editErrors = { category: this.t('err_category') }; return; }
        const want = this.payload(f);
        if (want.amount !== rupeesPlain(x.amount_paisa)) body.amount = want.amount;
        if (want.occurred_on !== x.occurred_on) body.occurred_on = want.occurred_on;
        if (want.payment_method !== x.payment_method) body.payment_method = want.payment_method;
        if (want.note !== (x.note || null)) body.note = want.note;
        const oldCat = x.subcategory_slug ? 's:' + x.subcategory_slug : 'c:' + x.category_slug;
        if (f.cat !== oldCat) { if (want.subcategory_slug) body.subcategory_slug = want.subcategory_slug; else body.category_slug = want.category_slug; }
        if (Object.keys(body).length === 1) { this.closeEdit(); return; }       // nothing changed
        this.saving = true;
        const r = await this.api('PATCH', '/api/expenses/' + x.id, body);
        this.saving = false;
        if (r.status === 409 && r.data && r.data.detail && r.data.detail.code === 'version_conflict') {
          this.closeEdit(); this.say(this.t('err_conflict')); await this.reload(); return;
        }
        if (r.status === 404) { this.closeEdit(); this.say(this.t('err_gone')); await this.reload(); return; }
        if (!r.ok) { this.editMessage = this.explain(r, this.editErrors); return; }
        this.closeEdit(); this.say(this.t('updated'));
        await this.reload();
      },

      // ── delete and undo ──────────────────────────────────────────────────────
      async remove(x) {
        const r = await this.api('DELETE', '/api/expenses/' + x.id);
        if (r.status === 404) { this.say(this.t('err_gone')); await this.reload(); return; }
        if (!r.ok) { this.say(this.explain(r, {}) || this.t('err_server')); return; }
        this.say(this.t('deleted'), x.id, UNDO_MS);
        await this.reload();
      },
      async undo() {
        const id = this.toast.undoId;
        if (!id) return;
        this.clearToast();
        const r = await this.api('POST', '/api/expenses/' + id + '/restore');
        if (!r.ok) { this.say(r.status === 404 ? this.t('err_gone') : this.explain(r, {}) || this.t('err_server')); return; }
        this.say(this.t('restored'));
        await this.reload();
      },

      // ── toast ────────────────────────────────────────────────────────────────
      say(text, undoId, ms) {
        clearTimeout(this.toastTimer);
        this.toast = { text, undoId: undoId || null };
        this.toastTimer = setTimeout(() => this.clearToast(), ms || 4000);
      },
      clearToast() { clearTimeout(this.toastTimer); this.toast = { text: '', undoId: null }; },
    };
  };
})();
