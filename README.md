# Quest Bot

Aiogram 3 asosidagi Telegram kvest boti. Interfeys tillari: o‘zbekcha, ruscha va inglizcha. Web-admin panel va reklama mavjud emas.

## Imkoniyatlar

- Faqat admin/superadmin kvest yaratadi.
- Title, description, public/private ko‘rinish, boshlanish vaqti, bosqichlar soni, umumiy vaqt.
- Har bosqichda savol, urinishlar soni, vaqt limiti va avtomatik yoki admin tekshiruvi.
- Keyingi bosqichni to‘g‘ri javobdan keyin darhol yoki oldindan belgilangan jadvalda yuborish.
- Public kvestlarni ko‘rish, qatnashish, status bo‘yicha saralash va reyting.
- Private kvestga bot deep-link orqali qo‘shilish.
- Kvestni Telegram guruh/kanaliga biriktirish; javoblar doimo botning private chatiga yuboriladi.
- Qatnashchini kvest doirasida sababli yoki sababsiz bloklash va unga ogohlantirish yuborish.
- Superadmin paneli: admin tayinlash, barcha kvestlarni boshqarish, arxivlash, statistika, audit log, chat sozlamalari, sahifa o‘lchami va ZIP eksport.
- Superadmin yoki kvest admini bilan bot ichida ticket-chat.

To‘liq funksional reja, qoida va Telegram API cheklovlari: [`plan.md`](plan.md).

## Talablar

- Python 3.11+
- Telegram bot tokeni (BotFather orqali)
- Kamida bitta superadminning raqamli Telegram ID raqami

## O‘rnatish va ishga tushirish

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

`.env` faylida quyidagilarni kiriting:

```dotenv
BOT_TOKEN=123456:replace-with-real-token
SUPERADMIN_IDS=123456789
DATABASE_PATH=data/quest_bot.sqlite3
SCHEDULER_INTERVAL_SECONDS=10
```

Bir nechta superadmin bo‘lsa, ID’larni vergul bilan ajrating:

```dotenv
SUPERADMIN_IDS=123456789,987654321
```

Botni ishga tushiring:

```bash
python main.py
```

Superadmin botni kamida bir marta `/start` bilan ochishi lozim. SQLite bazasi `DATABASE_PATH` ko‘rsatgan faylda saqlanadi; production’da bu joy persistent disk/volume bo‘lishi kerak.

## Guruh yoki kanal ulash

1. Botni kerakli Telegram guruh yoki kanalga qo‘shing va admin qiling.
2. Kanal uchun botga xabar chiqarish (`can_post_messages`), chatni tozalash uchun a’zolarni cheklash (`can_restrict_members`), bir kishilik invite link yaratish uchun a’zo taklif qilish (`can_invite_users`) huquqlarini bering.
3. Guruh/kanalda `/chatid` buyrug‘ini yuboring. Bot chat ID’ni qaytaradi.
4. Superadmin botdagi **Admin paneli → Sozlamalar → Guruh/kanallar → Qo‘shish** orqali chat ID’ni ro‘yxatdan o‘tkazadi.
5. Kvest yaratishda admin ro‘yxatdagi chatni tanlaydi yoki **Faqat bot** variantidan foydalanadi.
6. A’zolarni start paytida chiqarish kerak bo‘lsa, superadmin chat sozlamasida bu funksiyani yoqib, chiqarilmaydigan ID’larni whitelistga qo‘shadi.

Savollar guruh/kanalda e’lon qilinadi, javoblar esa botga private yuboriladi. Jadval rejimida har bir rejalashtirilgan bosqich e’lon qilinadi. Darhol o‘tish rejimida faqat birinchi bosqich umumiy chatga chiqariladi; keyingi bosqichlar har qatnashchiga alohida yuboriladi, shunda javoblar orqali savol oshkor bo‘lmaydi.

> **A’zolarni chiqarish cheklovi:** Telegram botlar chatdagi barcha mavjud a’zolar ro‘yxatini so‘rab ololmaydi. Bot chat ro‘yxatga olinganidan keyin `chat_member` yangilanishlarida kuzatgan a’zolarnigina tekshiradi. Botni admin qilish va `chat_member` update’larini qabul qilish kerak. Oldindan mavjud a’zolarni qo‘shish yoki to‘liq tozalash kerak bo‘lsa, alohida user ID ro‘yxati talab qilinadi.

## Adminlardan foydalanish

- Superadmin: `/admin` yoki menyudagi **Admin paneli**.
- Yangi admin: **Adminlarni boshqarish → Qo‘shish**, so‘ng raqamli Telegram ID yuborish. Yangi admin botni `/start` bilan ochgach, o‘z kvestlarini yaratishi mumkin.
- Kvest yaratish: **Kvest yaratish** wizardidagi ko‘rsatmalarga amal qiling.
- Admin o‘z kvestlarida reyting, ishtirokchilar va tekshiruvdagi javoblarni boshqaradi.
- Arxivlash faqat superadmin uchun.
- Private kvest yaratilgach, bot qo‘shilish deep-linkini beradi. Uni yana olish uchun kvest boshqaruvidagi **Kvest taklif havolasini olish** tugmasidan foydalaning.
- Kvestga qo‘shilgan qatnashchiga biriktirilgan chat uchun bir kishilik link yuboriladi: kvest boshlanishidan oldin qo‘shilganga tozalashdan so‘ng, start paytida; faol kvestga qo‘shilganga darhol. Keyin **Chatga bir kishilik havola** tugmasi bilan qayta ko‘rish mumkin. Telegram invite link `member_limit=1` bilan, expiry vaqti belgilanmasdan yaratiladi. Har qatnashchiga link faqat bir marta yaratiladi.

## Vaqt va javob qoidalari

- Kvest vaqti `Asia/Tashkent` mahalliy vaqtida kiritiladi; SQLite’da UTC saqlanadi.
- Sana formati: `YYYY-MM-DD HH:MM`, masalan `2026-10-03 18:30`.
- Umumiy yoki bosqich vaqti daqiqada kiritiladi; `0` — cheklov yo‘q.
- Avtomatik tekshiruvda javob matni aynan mos bo‘lishi kerak; faqat Unicode NFC va bosh/oxiridagi bo‘sh joylar normallashtiriladi. Katta-kichik harf va tinish belgisi muhim.
- Urinishlar tugasa yoki bosqich vaqti tugasa, qatnashchi kvestdan `failed` holatiga o‘tadi.
- Bir nechta kvestda bir vaqtda ochiq savol bo‘lsa, javob yuborishdan avval kvestni tanlash tugmalari ko‘rsatiladi.

## Asosiy buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `/start` | Botni ochish yoki kvest taklif havolasi orqali qo‘shilish |
| `/menu` | Asosiy menyu |
| `/quests` | Public kvestlar |
| `/admin` | Admin paneli |
| `/support` | Superadmin yoki kvest adminiga murojaat |
| `/language` | Interfeys tilini o‘zgartirish |
| `/help` | Foydalanish yo‘riqnomasi |
| `/cancel` | Joriy wizard/amalni bekor qilish |
| `/chatid` | Guruh/kanal ID’sini ko‘rsatish |

## Testlar

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Testlar SQLite’dagi kvest yaratish, private token tekshiruvi, javob urinishlari, admin review va role sozlamalarini qamrab oladi.

## Ishga tushirishdan oldingi xavfsizlik

- `.env` faylini Git’ga qo‘shmang; tokenni hech qayerda log qilmang.
- `data/quest_bot.sqlite3` uchun backup siyosatini belgilang.
- Admin bot bilan shaxsiy chatni ochib, `/start` qilishi kerak, aks holda bot unga savol, review yoki support xabarini yubora olmaydi.
- Private kvest bot deep-linki shareable; bitta foydalanuvchi bilan cheklangan kanal/guruh linkidan alohida.
