/*
 * The expense assistant inside the chat page (Path step 5b). It is mixed into the chatApp() component of
 * pages/chatbot.html (Object.assign), so `this` is that component: it uses `this.messages`, `this.inputText`.
 * Everything here talks only to /api/expense-ai/*; the chat message itself still goes to /chat/message.
 *
 * Rules this file keeps (tests/test_expense_chat_ui.py and tests/test_expense_chat_js.py check them):
 *   - The person's words (the note of an entry) are only ever shown with x-text / textContent, never as HTML.
 *   - Every POST carries X-Requested-With: ShariahEase (authHeaders() does not add it).
 *   - A button that sends a request is disabled while the request is in flight, and a second tap does nothing.
 *   - The page does no money arithmetic: amounts are the server's `amount_display` strings.
 *   - Nothing pretends a save happened: after a lost connection on Confirm/Undo the text says it cannot tell.
 *
 * URDU TEXT: every string in the `ur` block is marked UR and every string in the `ro` block (Roman Urdu) is marked RU:
 * all of it is awaiting review by a native speaker (see the review list in the hand-off).
 */
(function () {
  'use strict';

  const WRITE_HEADER = { 'X-Requested-With': 'ShariahEase' };
  const API = '/api/expense-ai';
  const UNDO_MS = 60 * 60 * 1000;          // the server's undo window (pending_actions.UNDO_WINDOW)
  const TIMEOUT_MS = 20000;                // a card request that does not answer in 20 s is reported, never an endless spinner
  const LANG_KEY = 'se_xlang';
  const PANEL_LANGS = ['en', 'ur', 'ro'];
  const CARD_LANG = { en: 'en', ur: 'ur', roman_ur: 'ro' };      // card.lang -> text block

  // Example messages: every one is a message from the gold set (tests/gold_set.py), so each is known to be understood.
  const EXAMPLES = {
    en: [
      { kind: 'log', text: 'Spent Rs 500 on chai today' },
      { kind: 'query', text: 'How much did I spend this month?' },
      { kind: 'delete', text: 'delete my last entry' },
    ],
    ur: [   // UR
      { kind: 'log', text: 'آج چائے پر 50 روپے خرچ کیے' },
      { kind: 'query', text: 'اس مہینے کتنا خرچ ہوا؟' },
      { kind: 'delete', text: 'کرایہ والی انٹری ڈیلیٹ کر دو' },
    ],
    ro: [   // RU
      { kind: 'log', text: 'aaj chai pe 50 rupay kharch kiye' },
      { kind: 'query', text: 'is mahine kitna kharcha hua?' },
      { kind: 'delete', text: 'pichli entry delete kar do' },
    ],
  };

  const TEXT = {
    en: {
      // the card
      c_create: 'Please check this before it is saved', c_update: 'Change this entry?', c_delete: 'Delete this entry?',
      confirm: 'Confirm', cancel: 'Cancel', undo: 'Undo', change_cat: 'Change category',
      pick_hint: 'Which entry do you mean? Tap it, or type its number.',
      will_change: 'This will change', delete_will: 'This entry will be deleted. You can undo it for 1 hour.',
      l_amount: 'Amount', l_category: 'Category', l_date: 'Date', l_note: 'Note',
      valid_until: 'Valid until {time}', restored: 'You have a card waiting from earlier.',
      // what happened
      o_saved: 'Saved. You can undo this for 1 hour while this page stays open.',
      o_changed: 'Changed. You can undo this for 1 hour while this page stays open.',
      o_deleted: 'Deleted. You can undo this for 1 hour while this page stays open.',
      o_cancelled: 'Cancelled. Nothing was saved.', o_undone: 'Undone.',
      o_expired: 'This card has expired. Nothing was saved. Send your message again.',
      o_answered: 'This card was answered in the chat. See the reply below.',
      o_superseded: 'A newer card replaced this one.',
      o_stale: 'This entry changed after the card was made, so nothing was changed. Send your request again.',
      e_used: 'This card was already used. Check your expenses to see the result.',
      e_cancelled: 'This card was cancelled. Nothing was saved.',
      e_not_found: 'This card is no longer available.',
      e_conflict: 'The entry changed, so nothing was saved. Send your request again.',
      e_not_chosen: 'Choose which entry you mean first.',
      e_undo_over: 'The 1 hour for undo is over. You can still edit or delete it on the Expenses page.',
      e_bad_choice: 'That choice is not available for this card.',
      e_invalid: 'The server did not accept this entry. Nothing was saved.',
      e_rate: 'Too many taps. Try again in {n} seconds.',
      e_net: 'No connection. Nothing was changed. Please try again.',
      e_net_unsure: 'No connection. We cannot tell whether it went through. Tap again to check: it will never happen twice.',
      e_server: 'Something went wrong. Please try again.',
      e_server_unsure: 'Something went wrong on the server. We cannot tell whether it went through. Tap again to check: it will never happen twice.',
      // the panel
      x_help: 'Expense help',
      w_hint: 'You can also log your expenses and ask about them here. Tap "Expense help" for examples.',
      s_used: '{used} of {limit} AI messages used this month',
      s_off: 'The expense assistant is switched off for now. You can still add expenses by hand.',
      s_breaker: 'The assistant has reached its limit for today and will work again tomorrow. You can still add expenses by hand.',
      s_limit: 'You have used all {limit} AI messages for this month. Adding expenses by hand stays free and unlimited.',
      s_failed: 'Could not check your AI message count.',
      s_login: 'Log in to log and check your expenses by chat. Without logging in, this chat answers Islamic finance questions only.',
      login: 'Log in', link_manual: 'Open the Expenses page',
      p_examples: 'Try one of these (tap to put it in the box, then change it to your own):',
      p_voice: 'Voice messages answer Islamic finance questions only. To log or ask about expenses, type your message.',
      pro_btn: 'Notify me about Pro', pro_busy: 'Sending…', pro_thanks: 'Thanks, we will tell you.',
      pro_note: 'Nothing to pay. We only note that you are interested.',
    },
    ur: {   // UR: all of this awaits native-speaker review
      c_create: 'محفوظ کرنے سے پہلے اسے دیکھ لیں', c_update: 'کیا یہ اندراج بدلنا ہے؟', c_delete: 'کیا یہ اندراج حذف کرنا ہے؟',
      confirm: 'تصدیق کریں', cancel: 'منسوخ کریں', undo: 'واپس کریں', change_cat: 'زمرہ بدلیں',
      pick_hint: 'آپ کون سا اندراج مراد لے رہے ہیں؟ اسے دبائیں یا اس کا نمبر لکھیں۔',
      will_change: 'یہ تبدیل ہوگا', delete_will: 'یہ اندراج حذف ہو جائے گا۔ آپ 1 گھنٹے تک واپس کر سکتے ہیں۔',
      l_amount: 'رقم', l_category: 'زمرہ', l_date: 'تاریخ', l_note: 'نوٹ',
      valid_until: 'یہ {time} تک درست ہے', restored: 'پہلے کا ایک کارڈ آپ کے انتظار میں ہے۔',
      o_saved: 'محفوظ ہو گیا۔ جب تک یہ صفحہ کھلا رہے، آپ 1 گھنٹے تک واپس کر سکتے ہیں۔',
      o_changed: 'بدل دیا گیا۔ جب تک یہ صفحہ کھلا رہے، آپ 1 گھنٹے تک واپس کر سکتے ہیں۔',
      o_deleted: 'حذف ہو گیا۔ جب تک یہ صفحہ کھلا رہے، آپ 1 گھنٹے تک واپس کر سکتے ہیں۔',
      o_cancelled: 'منسوخ ہو گیا۔ کچھ محفوظ نہیں ہوا۔', o_undone: 'واپس کر دیا گیا۔',
      o_expired: 'اس کارڈ کی مدت ختم ہو گئی۔ کچھ محفوظ نہیں ہوا۔ اپنا پیغام دوبارہ بھیجیں۔',
      o_answered: 'اس کارڈ کا جواب چیٹ میں دے دیا گیا۔ نیچے جواب دیکھیں۔',
      o_superseded: 'ایک نئے کارڈ نے اس کی جگہ لے لی۔',
      o_stale: 'کارڈ بننے کے بعد یہ اندراج بدل گیا، اس لیے کچھ نہیں بدلا۔ اپنی درخواست دوبارہ بھیجیں۔',
      e_used: 'یہ کارڈ پہلے ہی استعمال ہو چکا ہے۔ نتیجہ دیکھنے کے لیے اپنے اخراجات دیکھیں۔',
      e_cancelled: 'یہ کارڈ منسوخ ہو چکا ہے۔ کچھ محفوظ نہیں ہوا۔',
      e_not_found: 'یہ کارڈ اب دستیاب نہیں ہے۔',
      e_conflict: 'اندراج بدل گیا، اس لیے کچھ محفوظ نہیں ہوا۔ اپنی درخواست دوبارہ بھیجیں۔',
      e_not_chosen: 'پہلے منتخب کریں کہ آپ کون سا اندراج مراد لے رہے ہیں۔',
      e_undo_over: 'واپس کرنے کا 1 گھنٹہ ختم ہو گیا۔ آپ اسے اخراجات کے صفحے پر اب بھی بدل یا حذف کر سکتے ہیں۔',
      e_bad_choice: 'یہ انتخاب اس کارڈ کے لیے دستیاب نہیں ہے۔',
      e_invalid: 'سرور نے یہ اندراج قبول نہیں کیا۔ کچھ محفوظ نہیں ہوا۔',
      e_rate: 'بہت زیادہ بار دبایا گیا۔ {n} سیکنڈ بعد دوبارہ کوشش کریں۔',
      e_net: 'کنکشن نہیں ہے۔ کچھ نہیں بدلا۔ دوبارہ کوشش کریں۔',
      e_net_unsure: 'کنکشن نہیں ہے۔ ہم نہیں بتا سکتے کہ کام ہوا یا نہیں۔ دوبارہ دبا کر دیکھیں: یہ دو بار نہیں ہوگا۔',
      e_server: 'کچھ گڑبڑ ہو گئی۔ دوبارہ کوشش کریں۔',
      e_server_unsure: 'سرور پر کچھ گڑبڑ ہو گئی۔ ہم نہیں بتا سکتے کہ کام ہوا یا نہیں۔ دوبارہ دبا کر دیکھیں: یہ دو بار نہیں ہوگا۔',
      x_help: 'اخراجات میں مدد',
      w_hint: 'آپ یہاں اپنے اخراجات درج بھی کر سکتے ہیں اور ان کے بارے میں پوچھ بھی سکتے ہیں۔ مثالوں کے لیے "اخراجات میں مدد" دبائیں۔',
      s_used: 'اس مہینے {limit} میں سے {used} اے آئی پیغامات استعمال ہوئے',
      s_off: 'خرچ کا اسسٹنٹ فی الحال بند ہے۔ آپ اب بھی ہاتھ سے خرچ شامل کر سکتے ہیں۔',
      s_breaker: 'اسسٹنٹ آج کی حد تک پہنچ گیا ہے اور کل پھر چلے گا۔ آپ اب بھی ہاتھ سے خرچ شامل کر سکتے ہیں۔',
      s_limit: 'آپ نے اس مہینے کے تمام {limit} اے آئی پیغامات استعمال کر لیے ہیں۔ ہاتھ سے خرچ شامل کرنا مفت اور لامحدود رہتا ہے۔',
      s_failed: 'آپ کے اے آئی پیغامات کی گنتی معلوم نہیں ہو سکی۔',
      s_login: 'چیٹ میں اپنے اخراجات درج کرنے اور دیکھنے کے لیے لاگ اِن کریں۔ لاگ اِن کے بغیر یہ چیٹ صرف اسلامی مالیات کے سوالوں کے جواب دیتی ہے۔',
      login: 'لاگ اِن', link_manual: 'اخراجات کا صفحہ کھولیں',
      p_examples: 'ان میں سے کوئی آزمائیں (دبائیں تو ڈبے میں آ جائے گا، پھر اسے اپنے مطابق بدل لیں):',
      p_voice: 'آواز والے پیغامات صرف اسلامی مالیات کے سوالوں کے جواب دیتے ہیں۔ اخراجات درج کرنے یا ان کے بارے میں پوچھنے کے لیے پیغام لکھیں۔',
      pro_btn: 'پرو کے بارے میں مجھے بتائیں', pro_busy: 'بھیجا جا رہا ہے…', pro_thanks: 'شکریہ، ہم آپ کو بتا دیں گے۔',
      pro_note: 'کچھ ادا نہیں کرنا۔ ہم صرف نوٹ کرتے ہیں کہ آپ دلچسپی رکھتے ہیں۔',
    },
    ro: {   // RU: Roman Urdu, all of this awaits native-speaker review
      c_create: 'Save hone se pehle isay check kar lein', c_update: 'Kya ye entry badalni hai?', c_delete: 'Kya ye entry delete karni hai?',
      confirm: 'Confirm karein', cancel: 'Cancel karein', undo: 'Wapas karein', change_cat: 'Category badlein',
      pick_hint: 'Aap kaun si entry kehna chahte hain? Us par tap karein ya uska number likhein.',
      will_change: 'Ye badal jayega', delete_will: 'Ye entry delete ho jayegi. Aap 1 ghante tak wapas kar sakte hain.',
      l_amount: 'Raqam', l_category: 'Category', l_date: 'Tareekh', l_note: 'Note',
      valid_until: '{time} tak valid hai', restored: 'Pehle ka ek card aapke intezar mein hai.',
      o_saved: 'Save ho gaya. Jab tak ye page khula hai, aap 1 ghante tak wapas kar sakte hain.',
      o_changed: 'Badal diya gaya. Jab tak ye page khula hai, aap 1 ghante tak wapas kar sakte hain.',
      o_deleted: 'Delete ho gaya. Jab tak ye page khula hai, aap 1 ghante tak wapas kar sakte hain.',
      o_cancelled: 'Cancel ho gaya. Kuch save nahi hua.', o_undone: 'Wapas kar diya gaya.',
      o_expired: 'Is card ki muddat khatam ho gayi. Kuch save nahi hua. Apna message dobara bhejein.',
      o_answered: 'Is card ka jawab chat mein de diya gaya. Neeche jawab dekhein.',
      o_superseded: 'Ek naye card ne iski jagah le li.',
      o_stale: 'Card banne ke baad ye entry badal gayi, is liye kuch nahi badla. Apni request dobara bhejein.',
      e_used: 'Ye card pehle hi istemal ho chuka hai. Nateeja dekhne ke liye apne expenses dekhein.',
      e_cancelled: 'Ye card cancel ho chuka hai. Kuch save nahi hua.',
      e_not_found: 'Ye card ab maujood nahi hai.',
      e_conflict: 'Entry badal gayi, is liye kuch save nahi hua. Apni request dobara bhejein.',
      e_not_chosen: 'Pehle chunein ke aap kaun si entry kehna chahte hain.',
      e_undo_over: 'Wapas karne ka 1 ghanta khatam ho gaya. Aap ise Expenses page par ab bhi badal ya delete kar sakte hain.',
      e_bad_choice: 'Ye intekhab is card ke liye maujood nahi hai.',
      e_invalid: 'Server ne ye entry qubool nahi ki. Kuch save nahi hua.',
      e_rate: 'Bohat zyada baar tap kiya gaya. {n} second baad dobara koshish karein.',
      e_net: 'Connection nahi hai. Kuch nahi badla. Dobara koshish karein.',
      e_net_unsure: 'Connection nahi hai. Hum nahi bata sakte ke kaam hua ya nahi. Dobara tap karke dekhein: ye do baar nahi hoga.',
      e_server: 'Kuch gadbad ho gayi. Dobara koshish karein.',
      e_server_unsure: 'Server par kuch gadbad ho gayi. Hum nahi bata sakte ke kaam hua ya nahi. Dobara tap karke dekhein: ye do baar nahi hoga.',
      x_help: 'Kharchay mein madad',
      w_hint: 'Aap yahan apne expenses likh bhi sakte hain aur unke baare mein pooch bhi sakte hain. Misaalon ke liye "Kharchay mein madad" dabayein.',
      s_used: 'Is mahine {limit} mein se {used} AI messages istemal hue',
      s_off: 'Expense assistant abhi band hai. Aap ab bhi haath se expense add kar sakte hain.',
      s_breaker: 'Assistant aaj ki had tak pahunch gaya hai aur kal phir chalega. Aap ab bhi haath se expense add kar sakte hain.',
      s_limit: 'Aap ne is mahine ke tamam {limit} AI messages istemal kar liye hain. Haath se expense add karna muft aur be-had rehta hai.',
      s_failed: 'Aapke AI messages ki ginti maloom nahi ho saki.',
      s_login: 'Chat mein apne expenses likhne aur dekhne ke liye login karein. Login ke baghair ye chat sirf Islamic finance ke sawalon ke jawab deti hai.',
      login: 'Login', link_manual: 'Expenses ka page kholein',
      p_examples: 'In mein se koi aazmayein (tap karein to box mein aa jayega, phir ise apne hisaab se badal lein):',
      p_voice: 'Awaz wale messages sirf Islamic finance ke sawalon ke jawab dete hain. Expenses likhne ya unke baare mein poochne ke liye message type karein.',
      pro_btn: 'Pro ke baare mein mujhe batayein', pro_busy: 'Bheja ja raha hai…', pro_thanks: 'Shukriya, hum aapko bata denge.',
      pro_note: 'Kuch ada nahi karna. Hum sirf note karte hain ke aap dilchaspi rakhte hain.',
    },
  };

  // ── small helpers ───────────────────────────────────────────────────────────────

  function fmt(template, vars) {
    return String(template).replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m));
  }

  function newId() {                       // a UUID; client_msg_id may be at most 64 characters
    const c = window.crypto || window.msCrypto;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    const b = new Uint8Array(16);
    if (c && c.getRandomValues) c.getRandomValues(b); else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    return h.slice(0, 8) + '-' + h.slice(8, 12) + '-' + h.slice(12, 16) + '-' + h.slice(16, 20) + '-' + h.slice(20);
  }

  function fmtDate(iso, lang) {            // "2026-11-25" -> "25 Nov 2026" (Urdu month names for Urdu); the date is a plain calendar date
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    if (!m) return String(iso || '');
    try {
      const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
      return new Intl.DateTimeFormat(lang === 'ur' ? 'ur-PK' : 'en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(d);
    } catch (e) { return String(iso); }
  }

  function errorCode(data) {               // FastAPI: {"detail": {"code": "stale"}}; a body refused by validation has a list
    if (data && data.detail && typeof data.detail === 'object' && !Array.isArray(data.detail)) return data.detail.code || '';
    if (data && data.detail && Array.isArray(data.detail)) return 'invalid';
    return '';
  }

  function safeStorage(op, key, value) {
    try { return op === 'get' ? window.localStorage.getItem(key) : window.localStorage.setItem(key, value); } catch (e) { return null; }
  }

  // ── the component part ──────────────────────────────────────────────────────────

  window.expenseChat = function expenseChat() {
    return {
      xLoggedIn: false,                    // same rule as base.html: a stored login token
      xLang: 'en',                         // language of the panel (examples, status line, Pro button)
      xOpen: false,
      xStatus: null,                       // the last GET /status answer
      xStatusFailed: false,
      xTick: 0,                            // moves every 15 s so expiry and the undo hour update on screen
      xOffset: 0,                          // server clock minus this device's clock (from the Date header)
      xTimer: null,
      xProBusy: false, xProDone: false, xProError: '',
      xFailedSend: null,                   // {text, id}: the message whose send failed, so a retry reuses the same id
      xExamples: EXAMPLES,
      xPanelLangs: PANEL_LANGS,

      // lifecycle ---------------------------------------------------------------

      async xInit() {
        this.xLoggedIn = !!safeStorage('get', 'se_token');
        const saved = safeStorage('get', LANG_KEY);
        if (PANEL_LANGS.indexOf(saved) >= 0) this.xLang = saved;
        this.xTick = Date.now();
        this.xOpen = false;                  // closed: an open panel would cover the chat; the welcome text points to it
        this.xTimer = setInterval(() => { this.xTick = Date.now(); }, 15000);
        if (this.xLoggedIn) await Promise.all([this.xRefreshStatus(), this.xLoadPending()]);
      },

      xDestroy() { if (this.xTimer) clearInterval(this.xTimer); this.xTimer = null; },

      // text --------------------------------------------------------------------

      xt(key, vars) {                      // panel text
        const block = TEXT[this.xLang] || TEXT.en;
        return fmt(key in block ? block[key] : TEXT.en[key], vars);
      },

      ct(card, key, vars) {                // card text, in the language of the card (the language of the reply that came with it)
        const block = TEXT[CARD_LANG[card && card.lang] || 'en'];
        return fmt(key in block ? block[key] : TEXT.en[key], vars);
      },

      xSetLang(lang) {
        if (PANEL_LANGS.indexOf(lang) < 0) return;
        this.xLang = lang;
        safeStorage('set', LANG_KEY, lang);
      },

      xDate(iso, lang) { return fmtDate(iso, lang); },

      xTime(iso) {
        const t = Date.parse(iso);
        if (isNaN(t)) return '';
        return new Date(t).toLocaleTimeString('en-PK', { hour: '2-digit', minute: '2-digit' });
      },

      xCatName(item, card) { return card && card.lang === 'ur' ? item.category_name_ur : item.category_name_en; },
      xAltName(alt, card) { return card && card.lang === 'ur' ? alt.name_ur : alt.name_en; },

      xTitle(card) { return this.ct(card, card.kind === 'update' ? 'c_update' : card.kind === 'delete' ? 'c_delete' : 'c_create'); },

      // time on the server's clock ------------------------------------------------

      xClock() { return this.xTick + this.xOffset; },          // reads xTick, so a template that calls it refreshes with the timer

      xNoteDate(res) {
        try {
          const t = Date.parse(res.headers.get('Date'));
          if (!isNaN(t)) this.xOffset = t - Date.now();
        } catch (e) { /* no Date header: keep the old offset */ }
      },

      // what a card can do right now -----------------------------------------------

      xExpiredNow(card) { return Date.parse(card.expires_at) <= this.xClock(); },

      xActive(msg) {                       // buttons are offered only here
        return !!(msg && msg.card && msg.card.status === 'pending' && !msg.outcome && !msg.superseded && !this.xExpiredNow(msg.card));
      },

      xShowExpired(msg) {
        return !!(msg && msg.card && !msg.outcome && !msg.superseded && (msg.card.status === 'expired' || (msg.card.status === 'pending' && this.xExpiredNow(msg.card))));
      },

      xCanUndo(msg) { return !!(msg && msg.undoUntil && msg.undoUntil > this.xClock()); },

      xNeedsPick(card) { return !!card && card.kind !== 'create' && (card.chosen === null || card.chosen === undefined); },

      xIsEditCard(card) { return !!card && card.kind !== 'create'; },

      xShowCandidates(card) { return this.xIsEditCard(card) && (this.xNeedsPick(card) || card.items.length > 1); },

      xPickMode(msg) { return this.xActive(msg) && this.xShowCandidates(msg.card); },

      xVisibleItems(msg) {                 // once an edit card is settled, only the entry it was about stays on screen
        const c = msg.card;
        if (this.xIsEditCard(c) && !this.xActive(msg) && !this.xNeedsPick(c)) return c.items.filter((i) => i.index === c.chosen);
        return c.items;
      },

      xChosen(card) { return this.xIsEditCard(card) && !this.xNeedsPick(card) ? card.items[card.chosen] || null : null; },

      xChangeRows(card) {                  // old -> new, built from the server's strings
        const item = this.xChosen(card), ch = card && card.changes;
        if (!item || !ch) return [];
        const rows = [];
        if (ch.amount_paisa !== null && ch.amount_paisa !== undefined) rows.push({ label: this.ct(card, 'l_amount'), from: item.amount_display, to: ch.amount_display });
        if (ch.category_slug) rows.push({ label: this.ct(card, 'l_category'), from: this.xCatName(item, card), to: card.lang === 'ur' ? ch.category_name_ur : ch.category_name_en });
        return rows;
      },

      // requests ----------------------------------------------------------------

      async xApi(method, path, body) {
        const headers = Object.assign({}, authHeaders(), method === 'GET' ? {} : WRITE_HEADER);
        const ctl = typeof AbortController === 'function' ? new AbortController() : null;
        const timer = ctl ? setTimeout(() => ctl.abort(), TIMEOUT_MS) : null;
        let res;
        try {
          res = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), signal: ctl ? ctl.signal : undefined });
        } catch (e) {
          return { net: true };            // no answer: the outcome is unknown
        } finally {
          if (timer) clearTimeout(timer);
        }
        this.xNoteDate(res);
        let data = null;
        try { data = await res.json(); } catch (e) { data = null; }
        return { res, ok: res.ok, status: res.status, data, code: errorCode(data) };
      },

      xRetrySeconds(r) {
        const h = parseInt(r.res && r.res.headers && r.res.headers.get('Retry-After'), 10);
        const d = r.data && r.data.detail && r.data.detail.retry_after;
        return h > 0 ? h : (d > 0 ? d : 60);
      },

      async xRefreshStatus() {
        const r = await this.xApi('GET', '/status');
        if (r.net) { this.xStatusFailed = true; return; }
        if (r.status === 401) { this.xLoggedIn = false; return; }      // not logged in after all: show the log-in note, no redirect
        if (!r.ok || !r.data) { this.xStatusFailed = true; return; }
        this.xStatus = r.data;
        this.xStatusFailed = false;
        if (r.data.upgrade_interest) this.xProDone = true;
      },

      async xLoadPending() {                // expense chat messages are not stored, so a reload would lose a waiting card
        const r = await this.xApi('GET', '/pending');
        if (r.net) return;
        if (r.status === 401) { this.xLoggedIn = false; return; }
        const card = r.ok && r.data ? r.data.card : null;
        if (!card || !card.id || card.status !== 'pending') return;
        if (this.messages.some((m) => m.card && m.card.id === card.id)) return;
        this.messages.push(this.xCardMessage(card, this.ct(card, 'restored'), this.formatTime()));
      },

      // sending -----------------------------------------------------------------

      xClientId(text) {                    // a retry of the same unsent message keeps its id: the server then answers with the same card, no second charge
        return this.xFailedSend && this.xFailedSend.text === text ? this.xFailedSend.id : newId();
      },

      xCardMessage(card, content, time) {
        return { role: 'assistant', type: 'text', content, time, sources: [], card, busy: false, outcome: '', error: '', undoUntil: 0, superseded: false, altOpen: -1 };
      },

      xAfterReply(data, time) {            // called by chatApp.sendMessage with the /chat/message answer
        this.xOpen = false;                  // the answer (or card) must be what is on screen
        let msg;
        if (data.card && data.card.id) {
          this.xSupersede();
          msg = this.xCardMessage(data.card, data.answer, time);
          msg.sources = data.sources || [];
        } else {
          msg = { role: 'assistant', type: 'text', content: data.answer, time, sources: data.sources || [] };
        }
        this.messages.push(msg);
        if (this.xLoggedIn) {
          if (!msg.card) this.xReconcile();
          this.xRefreshStatus();
        }
      },

      xSupersede() {                       // only the newest card is offered, like the typed "yes" (it acts on the newest)
        this.messages.forEach((m) => { if (m.card && !m.outcome && m.card.status === 'pending') m.superseded = true; });
      },

      async xReconcile() {                 // a typed "yes", "no" or other answer may have closed the card shown on screen
        let shown = null;
        for (let i = this.messages.length - 1; i >= 0; i--) { if (this.messages[i].card) { shown = this.messages[i]; break; } }
        if (!shown || !this.xActive(shown)) return;
        const r = await this.xApi('GET', '/pending');
        if (r.net || !r.ok || !r.data) return;
        if (!r.data.card) { if (!shown.outcome) shown.outcome = 'o_answered'; return; }
        if (r.data.card.id !== shown.card.id) { if (!shown.outcome) shown.outcome = 'o_answered'; return; }
        shown.card = r.data.card;
      },

      xUseExample(text) {                  // fills the box, never sends: an example would otherwise spend a message and could touch a real entry
        this.inputText = text;
        this.xOpen = false;
        const ta = document.querySelector('textarea');
        if (ta) { ta.focus(); if (this.autoResize) this.autoResize(ta); }
      },

      // card actions ----------------------------------------------------------------

      xConfirm(msg) { return this.xAct(msg, 'confirm'); },
      xCancel(msg) { return this.xAct(msg, 'cancel'); },
      xUndo(msg) { return this.xAct(msg, 'undo'); },
      xPick(msg, index) { return this.xAct(msg, 'pick', { index }); },
      xChoose(msg, item, slug) { return this.xAct(msg, 'choose', { item, category_slug: slug }); },

      xToggleAlt(msg, index) { msg.altOpen = msg.altOpen === index ? -1 : index; },

      async xAct(msg, action, body) {
        if (!msg || !msg.card || msg.busy) return;            // a second tap while a request is in flight does nothing
        this.xTick = Date.now();                              // judge expiry by the clock now, not by the last timer tick
        if (action === 'undo' ? !this.xCanUndo(msg) : !this.xActive(msg)) return;
        msg.busy = true;
        msg.error = '';
        const card = msg.card;
        const unsure = action === 'confirm' || action === 'undo';   // after these, "no answer" does not tell whether it happened
        try {
          const r = await this.xApi('POST', '/' + encodeURIComponent(card.id) + '/' + action, body);
          if (r.net) { msg.error = this.ct(card, unsure ? 'e_net_unsure' : 'e_net'); return; }
          if (r.status === 401 && handleAuthError(r.res)) return;
          if (r.ok) { this.xApplyOk(msg, action, r.data || {}); return; }
          this.xApplyError(msg, action, r, unsure);
        } finally {
          msg.busy = false;
        }
      },

      xApplyOk(msg, action, data) {
        const card = msg.card;
        msg.altOpen = -1;
        if (action === 'confirm') {
          const kind = data.kind || card.kind;
          msg.card = Object.assign({}, card, { status: 'confirmed' });
          msg.outcome = kind === 'update' ? 'o_changed' : kind === 'delete' ? 'o_deleted' : 'o_saved';
          msg.undoUntil = Date.now() + this.xOffset + UNDO_MS;
        } else if (action === 'cancel') {
          msg.card = Object.assign({}, card, { status: 'cancelled' });
          msg.outcome = 'o_cancelled';
        } else if (action === 'undo') {
          msg.outcome = 'o_undone';
          msg.undoUntil = 0;
        } else if (data.card) {            // choose, pick: the server sends the card as it is now
          msg.card = data.card;
        }
      },

      xApplyError(msg, action, r, unsure) {
        const card = msg.card;
        const stop = (key, status) => { msg.outcome = key; if (status) msg.card = Object.assign({}, card, { status }); };
        if (r.status === 429) { msg.error = this.ct(card, 'e_rate', { n: this.xRetrySeconds(r) }); return; }
        switch (r.code) {
          case 'already_used': stop('e_used'); return;
          case 'cancelled': stop('e_cancelled', 'cancelled'); return;
          case 'expired': stop('o_expired', 'expired'); return;
          case 'stale': stop('o_stale', 'cancelled'); return;
          case 'not_found': stop('e_not_found'); return;
          case 'conflict': stop('e_conflict'); return;
          case 'not_chosen': msg.error = this.ct(card, 'e_not_chosen'); return;
          case 'bad_choice': msg.error = this.ct(card, 'e_bad_choice'); return;
          case 'invalid_input': msg.error = this.ct(card, 'e_invalid'); return;
          case 'undo_window_over': msg.undoUntil = 0; msg.error = this.ct(card, 'e_undo_over'); return;
          default: msg.error = this.ct(card, r.status >= 500 && unsure ? 'e_server_unsure' : 'e_server');
        }
      },

      // the status strip ----------------------------------------------------------

      xState() {
        if (!this.xLoggedIn) return 'guest';
        if (!this.xStatus) return this.xStatusFailed ? 'failed' : 'loading';
        if (this.xStatus.enabled === false) return 'off';
        if (this.xStatus.breaker_open) return 'breaker';
        if (this.xStatus.used >= this.xStatus.limit) return 'limit';
        return 'ok';
      },

      xStatusText() {
        const s = this.xState(), st = this.xStatus || {};
        if (s === 'ok') return this.xt('s_used', { used: st.used, limit: st.limit });
        if (s === 'limit') return this.xt('s_limit', { limit: st.limit });
        if (s === 'off') return this.xt('s_off');
        if (s === 'breaker') return this.xt('s_breaker');
        if (s === 'failed') return this.xt('s_failed');
        if (s === 'guest') return this.xt('s_login');
        return '';
      },

      xShowManualLink() { return ['off', 'breaker', 'limit'].indexOf(this.xState()) >= 0; },

      // the "Notify me about Pro" button -------------------------------------------

      async xPro() {
        if (this.xProBusy || this.xProDone || !this.xLoggedIn) return;
        this.xProBusy = true;
        this.xProError = '';
        try {
          const r = await this.xApi('POST', '/upgrade-interest');
          if (r.net) { this.xProError = this.xt('e_net'); return; }
          if (r.status === 401 && handleAuthError(r.res)) return;
          if (r.ok) { this.xProDone = true; return; }
          this.xProError = r.status === 429 ? this.xt('e_rate', { n: this.xRetrySeconds(r) }) : this.xt('e_server');
        } finally {
          this.xProBusy = false;
        }
      },
    };
  };

  window.expenseChat.TEXT = TEXT;          // for the tests
  window.expenseChat.EXAMPLES = EXAMPLES;
})();
