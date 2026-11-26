# Step 5b: text to review by a native speaker

Every string below is shown to users. Urdu (UR) and Roman Urdu (RU) were written by me and have NOT been reviewed by a native speaker. Please check meaning, politeness and spelling; send corrections as `key -> corrected text` and I will apply them in one pass. File: `frontend/static/js/expense_chat.js` (blocks `ur`, `ro`). {time}, {n}, {used}, {limit} are filled in by the page.

The nine example messages are taken verbatim from the gold set, so they are known to be understood by the assistant.

| key | English | UR (Urdu) | RU (Roman Urdu) |
|---|---|---|---|
| `c_create` | Please check this before it is saved | محفوظ کرنے سے پہلے اسے دیکھ لیں | Save hone se pehle isay check kar lein |
| `c_update` | Change this entry? | کیا یہ اندراج بدلنا ہے؟ | Kya ye entry badalni hai? |
| `c_delete` | Delete this entry? | کیا یہ اندراج حذف کرنا ہے؟ | Kya ye entry delete karni hai? |
| `confirm` | Confirm | تصدیق کریں | Confirm karein |
| `cancel` | Cancel | منسوخ کریں | Cancel karein |
| `undo` | Undo | واپس کریں | Wapas karein |
| `change_cat` | Change category | زمرہ بدلیں | Category badlein |
| `pick_hint` | Which entry do you mean? Tap it, or type its number. | آپ کون سا اندراج مراد لے رہے ہیں؟ اسے دبائیں یا اس کا نمبر لکھیں۔ | Aap kaun si entry kehna chahte hain? Us par tap karein ya uska number likhein. |
| `will_change` | This will change | یہ تبدیل ہوگا | Ye badal jayega |
| `delete_will` | This entry will be deleted. You can undo it for 1 hour. | یہ اندراج حذف ہو جائے گا۔ آپ 1 گھنٹے تک واپس کر سکتے ہیں۔ | Ye entry delete ho jayegi. Aap 1 ghante tak wapas kar sakte hain. |
| `l_amount` | Amount | رقم | Raqam |
| `l_category` | Category | زمرہ | Category |
| `l_date` | Date | تاریخ | Tareekh |
| `l_note` | Note | نوٹ | Note |
| `valid_until` | Valid until {time} | یہ {time} تک درست ہے | {time} tak valid hai |
| `restored` | You have a card waiting from earlier. | پہلے کا ایک کارڈ آپ کے انتظار میں ہے۔ | Pehle ka ek card aapke intezar mein hai. |
| `o_saved` | Saved. You can undo this for 1 hour while this page stays open. | محفوظ ہو گیا۔ جب تک یہ صفحہ کھلا رہے، آپ 1 گھنٹے تک واپس کر سکتے ہیں۔ | Save ho gaya. Jab tak ye page khula hai, aap 1 ghante tak wapas kar sakte hain. |
| `o_changed` | Changed. You can undo this for 1 hour while this page stays open. | بدل دیا گیا۔ جب تک یہ صفحہ کھلا رہے، آپ 1 گھنٹے تک واپس کر سکتے ہیں۔ | Badal diya gaya. Jab tak ye page khula hai, aap 1 ghante tak wapas kar sakte hain. |
| `o_deleted` | Deleted. You can undo this for 1 hour while this page stays open. | حذف ہو گیا۔ جب تک یہ صفحہ کھلا رہے، آپ 1 گھنٹے تک واپس کر سکتے ہیں۔ | Delete ho gaya. Jab tak ye page khula hai, aap 1 ghante tak wapas kar sakte hain. |
| `o_cancelled` | Cancelled. Nothing was saved. | منسوخ ہو گیا۔ کچھ محفوظ نہیں ہوا۔ | Cancel ho gaya. Kuch save nahi hua. |
| `o_undone` | Undone. | واپس کر دیا گیا۔ | Wapas kar diya gaya. |
| `o_expired` | This card has expired. Nothing was saved. Send your message again. | اس کارڈ کی مدت ختم ہو گئی۔ کچھ محفوظ نہیں ہوا۔ اپنا پیغام دوبارہ بھیجیں۔ | Is card ki muddat khatam ho gayi. Kuch save nahi hua. Apna message dobara bhejein. |
| `o_answered` | This card was answered in the chat. See the reply below. | اس کارڈ کا جواب چیٹ میں دے دیا گیا۔ نیچے جواب دیکھیں۔ | Is card ka jawab chat mein de diya gaya. Neeche jawab dekhein. |
| `o_superseded` | A newer card replaced this one. | ایک نئے کارڈ نے اس کی جگہ لے لی۔ | Ek naye card ne iski jagah le li. |
| `o_stale` | This entry changed after the card was made, so nothing was changed. Send your request again. | کارڈ بننے کے بعد یہ اندراج بدل گیا، اس لیے کچھ نہیں بدلا۔ اپنی درخواست دوبارہ بھیجیں۔ | Card banne ke baad ye entry badal gayi, is liye kuch nahi badla. Apni request dobara bhejein. |
| `e_used` | This card was already used. Check your expenses to see the result. | یہ کارڈ پہلے ہی استعمال ہو چکا ہے۔ نتیجہ دیکھنے کے لیے اپنے اخراجات دیکھیں۔ | Ye card pehle hi istemal ho chuka hai. Nateeja dekhne ke liye apne expenses dekhein. |
| `e_cancelled` | This card was cancelled. Nothing was saved. | یہ کارڈ منسوخ ہو چکا ہے۔ کچھ محفوظ نہیں ہوا۔ | Ye card cancel ho chuka hai. Kuch save nahi hua. |
| `e_not_found` | This card is no longer available. | یہ کارڈ اب دستیاب نہیں ہے۔ | Ye card ab maujood nahi hai. |
| `e_conflict` | The entry changed, so nothing was saved. Send your request again. | اندراج بدل گیا، اس لیے کچھ محفوظ نہیں ہوا۔ اپنی درخواست دوبارہ بھیجیں۔ | Entry badal gayi, is liye kuch save nahi hua. Apni request dobara bhejein. |
| `e_not_chosen` | Choose which entry you mean first. | پہلے منتخب کریں کہ آپ کون سا اندراج مراد لے رہے ہیں۔ | Pehle chunein ke aap kaun si entry kehna chahte hain. |
| `e_undo_over` | The 1 hour for undo is over. You can still edit or delete it on the Expenses page. | واپس کرنے کا 1 گھنٹہ ختم ہو گیا۔ آپ اسے اخراجات کے صفحے پر اب بھی بدل یا حذف کر سکتے ہیں۔ | Wapas karne ka 1 ghanta khatam ho gaya. Aap ise Expenses page par ab bhi badal ya delete kar sakte hain. |
| `e_bad_choice` | That choice is not available for this card. | یہ انتخاب اس کارڈ کے لیے دستیاب نہیں ہے۔ | Ye intekhab is card ke liye maujood nahi hai. |
| `e_invalid` | The server did not accept this entry. Nothing was saved. | سرور نے یہ اندراج قبول نہیں کیا۔ کچھ محفوظ نہیں ہوا۔ | Server ne ye entry qubool nahi ki. Kuch save nahi hua. |
| `e_rate` | Too many taps. Try again in {n} seconds. | بہت زیادہ بار دبایا گیا۔ {n} سیکنڈ بعد دوبارہ کوشش کریں۔ | Bohat zyada baar tap kiya gaya. {n} second baad dobara koshish karein. |
| `e_net` | No connection. Nothing was changed. Please try again. | کنکشن نہیں ہے۔ کچھ نہیں بدلا۔ دوبارہ کوشش کریں۔ | Connection nahi hai. Kuch nahi badla. Dobara koshish karein. |
| `e_net_unsure` | No connection. We cannot tell whether it went through. Tap again to check: it will never happen twice. | کنکشن نہیں ہے۔ ہم نہیں بتا سکتے کہ کام ہوا یا نہیں۔ دوبارہ دبا کر دیکھیں: یہ دو بار نہیں ہوگا۔ | Connection nahi hai. Hum nahi bata sakte ke kaam hua ya nahi. Dobara tap karke dekhein: ye do baar nahi hoga. |
| `e_server` | Something went wrong. Please try again. | کچھ گڑبڑ ہو گئی۔ دوبارہ کوشش کریں۔ | Kuch gadbad ho gayi. Dobara koshish karein. |
| `e_server_unsure` | Something went wrong on the server. We cannot tell whether it went through. Tap again to check: it will never happen twice. | سرور پر کچھ گڑبڑ ہو گئی۔ ہم نہیں بتا سکتے کہ کام ہوا یا نہیں۔ دوبارہ دبا کر دیکھیں: یہ دو بار نہیں ہوگا۔ | Server par kuch gadbad ho gayi. Hum nahi bata sakte ke kaam hua ya nahi. Dobara tap karke dekhein: ye do baar nahi hoga. |
| `x_help` | Expense help | اخراجات میں مدد | Kharchay mein madad |
| `w_hint` | You can also log your expenses and ask about them here. Tap "Expense help" for examples. | آپ یہاں اپنے اخراجات درج بھی کر سکتے ہیں اور ان کے بارے میں پوچھ بھی سکتے ہیں۔ مثالوں کے لیے "اخراجات میں مدد" دبائیں۔ | Aap yahan apne expenses likh bhi sakte hain aur unke baare mein pooch bhi sakte hain. Misaalon ke liye "Kharchay mein madad" dabayein. |
| `s_used` | {used} of {limit} AI messages used this month | اس مہینے {limit} میں سے {used} اے آئی پیغامات استعمال ہوئے | Is mahine {limit} mein se {used} AI messages istemal hue |
| `s_off` | The expense assistant is switched off for now. You can still add expenses by hand. | خرچ کا اسسٹنٹ فی الحال بند ہے۔ آپ اب بھی ہاتھ سے خرچ شامل کر سکتے ہیں۔ | Expense assistant abhi band hai. Aap ab bhi haath se expense add kar sakte hain. |
| `s_breaker` | The assistant has reached its limit for today and will work again tomorrow. You can still add expenses by hand. | اسسٹنٹ آج کی حد تک پہنچ گیا ہے اور کل پھر چلے گا۔ آپ اب بھی ہاتھ سے خرچ شامل کر سکتے ہیں۔ | Assistant aaj ki had tak pahunch gaya hai aur kal phir chalega. Aap ab bhi haath se expense add kar sakte hain. |
| `s_limit` | You have used all {limit} AI messages for this month. Adding expenses by hand stays free and unlimited. | آپ نے اس مہینے کے تمام {limit} اے آئی پیغامات استعمال کر لیے ہیں۔ ہاتھ سے خرچ شامل کرنا مفت اور لامحدود رہتا ہے۔ | Aap ne is mahine ke tamam {limit} AI messages istemal kar liye hain. Haath se expense add karna muft aur be-had rehta hai. |
| `s_failed` | Could not check your AI message count. | آپ کے اے آئی پیغامات کی گنتی معلوم نہیں ہو سکی۔ | Aapke AI messages ki ginti maloom nahi ho saki. |
| `s_login` | Log in to log and check your expenses by chat. Without logging in, this chat answers Islamic finance questions only. | چیٹ میں اپنے اخراجات درج کرنے اور دیکھنے کے لیے لاگ اِن کریں۔ لاگ اِن کے بغیر یہ چیٹ صرف اسلامی مالیات کے سوالوں کے جواب دیتی ہے۔ | Chat mein apne expenses likhne aur dekhne ke liye login karein. Login ke baghair ye chat sirf Islamic finance ke sawalon ke jawab deti hai. |
| `login` | Log in | لاگ اِن | Login |
| `link_manual` | Open the Expenses page | اخراجات کا صفحہ کھولیں | Expenses ka page kholein |
| `p_examples` | Try one of these (tap to put it in the box, then change it to your own): | ان میں سے کوئی آزمائیں (دبائیں تو ڈبے میں آ جائے گا، پھر اسے اپنے مطابق بدل لیں): | In mein se koi aazmayein (tap karein to box mein aa jayega, phir ise apne hisaab se badal lein): |
| `p_voice` | Voice messages answer Islamic finance questions only. To log or ask about expenses, type your message. | آواز والے پیغامات صرف اسلامی مالیات کے سوالوں کے جواب دیتے ہیں۔ اخراجات درج کرنے یا ان کے بارے میں پوچھنے کے لیے پیغام لکھیں۔ | Awaz wale messages sirf Islamic finance ke sawalon ke jawab dete hain. Expenses likhne ya unke baare mein poochne ke liye message type karein. |
| `pro_btn` | Notify me about Pro | پرو کے بارے میں مجھے بتائیں | Pro ke baare mein mujhe batayein |
| `pro_busy` | Sending… | بھیجا جا رہا ہے… | Bheja ja raha hai… |
| `pro_thanks` | Thanks, we will tell you. | شکریہ، ہم آپ کو بتا دیں گے۔ | Shukriya, hum aapko bata denge. |
| `pro_note` | Nothing to pay. We only note that you are interested. | کچھ ادا نہیں کرنا۔ ہم صرف نوٹ کرتے ہیں کہ آپ دلچسپی رکھتے ہیں۔ | Kuch ada nahi karna. Hum sirf note karte hain ke aap dilchaspi rakhte hain. |

## Example messages (gold-set sentences)

| kind | English | UR | RU |
|---|---|---|---|
| log | Spent Rs 500 on chai today | آج چائے پر 50 روپے خرچ کیے | aaj chai pe 50 rupay kharch kiye |
| query | How much did I spend this month? | اس مہینے کتنا خرچ ہوا؟ | is mahine kitna kharcha hua? |
| delete | delete my last entry | کرایہ والی انٹری ڈیلیٹ کر دو | pichli entry delete kar do |
