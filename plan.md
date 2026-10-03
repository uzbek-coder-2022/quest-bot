# Quest Bot — loyiha rejasi

## 1. Maqsad va chegaralar

Aiogram 3 asosidagi Telegram bot orqali kvest yaratish, o‘tkazish va qatnashchilarni boshqarish. Foydalanuvchi interfeysi o‘zbek, rus va ingliz tillarini qo‘llaydi. Web-admin panel va reklama bo‘lmaydi.

Birinchi versiya Telegram ichida ishlaydi; asosiy ma’lumotlar SQLite’da saqlanadi. Savol va kvest matnini admin qaysi tilda yozsa, kontent o‘sha tilda ko‘rsatiladi. Interfeys tarjimasi kvest savollarini avtomatik tarjima qilmaydi.

## 2. Funksional qismlar

### A. Ishtirokchi interfeysi
- `/start`, asosiy menyu, `/help`, tilni almashtirish.
- Public kvestlarni status bo‘yicha ko‘rish: kutilmoqda, davom etmoqda, yakunlangan va arxivlangan.
- Superadmin sozlaydigan sahifa o‘lchami: 5, 10, 20 yoki 50 ta yozuv.
- Public kvestga bot ichidan qo‘shilish; private kvestga bot deep-link taklifi orqali kirish.
- Javoblar bot bilan shaxsiy chatda yuboriladi.
- Kvest reytingi: to‘g‘ri yechilgan bosqichlar soni, yakunlash vaqti, keyin qo‘shilgan vaqt bo‘yicha.

### B. Kvest yaratish va bajarish
- Kvest maydonlari: title, description, public/private, boshlanish vaqti, umumiy vaqt, bosqichlar soni, savollar chati.
- Har bosqich uchun savol, avtomatik yoki qo‘lda tekshiruv, urinishlar soni va vaqt limiti.
- Keyingi bosqich rejimi:
  - **Darhol:** to‘g‘ri javobdan keyin shu qatnashchiga keyingi savol private yuboriladi.
  - **Jadval bo‘yicha:** har bosqich uchun belgilangan vaqtda savol e’lon qilinadi va qatnashchilarga yuboriladi.
- Avtomatik javob tekshiruvi aynan tenglikka asoslanadi: Unicode NFC va tashqi bo‘shliqlar normallashtiriladi; harf registri, tinish belgilari va ichki bo‘shliqlar farqi saqlanadi.
- Qo‘lda tekshiruvdagi javob kvest egasi va superadminlarga review tugmalari bilan yuboriladi.
- Urinishlar tugasa yoki bosqich vaqti tugasa, qatnashchining shu kvestdagi holati `failed` bo‘ladi.
- Umumiy vaqt tugashi, admin yakunlashi yoki barcha mavjud qatnashchilar yakuniy holatga kelishi bilan kvest yakunlanadi. Qatnashchisi yo‘q kvest umumiy vaqti tugamaguncha yoki admin yakunlamaguncha ochiq qoladi.

### C. Admin va superadmin
- Superadmin `.env` dagi Telegram ID orqali belgilanadi; superadmin botni `/start` bilan ochishi kerak.
- Superadmin numeric Telegram ID bo‘yicha admin tayinlaydi yoki olib tashlaydi.
- Admin faqat o‘zi yaratgan kvestni boshqaradi; superadmin barcha kvestlarni ko‘rishi va boshqarishi mumkin.
- Kvest boshqaruvi: reyting, qatnashchilar, qo‘lda tekshiriladigan javoblar, kvestni yakunlash.
- Qatnashchini kvest doirasida bloklash; sabab ixtiyoriy, lekin ogohlantirish yuboriladi. Blokni qaytarish ham mumkin.
- Arxivlash va arxivdan chiqarish faqat superadmin huquqi.
- Superadmin paneli: adminlar, barcha kvestlar, statistika, audit log, sahifa sozlamasi, guruh/kanallar va CSV/ZIP eksport.

### D. Guruh/kanal integratsiyasi
- Superadmin chatlarni ro‘yxatga oladi; har kvestda ro‘yxatdagi bitta guruh/kanal yoki “faqat bot” tanlanadi.
- Jadval rejimida bosqich savoli chatga bir marta e’lon qilinadi. Har bir qatnashchi botga private javob beradi.
- Darhol o‘tish rejimida faqat birinchi bosqich chatga e’lon qilinadi. Keyingi bosqichlar har qatnashchiga private yuboriladi; aks holda bir qatnashchining javobi boshqalarga keyingi savolni oshkor qilishi mumkin.
- Kvest boshlanishida chatni tozalash superadmin tomonidan chat kesimida yoqiladi. Bot kuzatgan a’zolar orasida whitelistga kiritilmaganlar chiqariladi.
- `chat_member` yangilanishlari chat ro‘yxatga olingandan keyingi a’zolik o‘zgarishlarini kuzatadi.

### E. Taklif havolalari
- Private kvest uchun bot deep-link tokeni yaratiladi; u private kvestga qo‘shilish uchun ishlaydi.
- Kvestga qo‘shilgan qatnashchiga biriktirilgan Telegram chat uchun alohida link yuboriladi. Kvest boshlanishidan oldin qo‘shilganlarga link tozalash amali tugagach, start paytida beriladi; faol kvestga qo‘shilganlarga darhol yuboriladi. Keyin botdan qayta olish mumkin. Bot `member_limit=1` va `expire_date` bermasdan invite link yaratadi; link birinchi a’zo qo‘shilgach ishlatish limitini tugatadi.
- Bir qatnashchi uchun yaratilgan chat linki bazada bir marta saqlanadi va qayta so‘ralganda aynan shu link qaytariladi.

### F. Murojaatlar va audit
- Foydalanuvchi superadmin yoki o‘zi qatnashgan kvest adminiga murojaat ochadi.
- Ticket ichidagi xabarlar bot orqali ikki tomonga yetkaziladi.
- Muhim admin amallari audit logga yoziladi; superadmin so‘nggi yozuvlarni ko‘radi va ma’lumotlarni eksport qiladi.

## 3. Rollar va huquqlar

| Amal | Qatnashchi | Admin | Superadmin |
|---|---:|---:|---:|
| Public kvestlarni ko‘rish va qo‘shilish | Ha | Ha | Ha |
| Kvest yaratish | Yo‘q | Ha | Ha |
| Kvestni boshqarish | Yo‘q | Faqat o‘z kvesti | Barcha kvestlar |
| Manual javobni tekshirish | Yo‘q | O‘z kvesti | Barcha kvestlar |
| Qatnashchini kvestdan bloklash | Yo‘q | O‘z kvesti | Barcha kvestlar |
| Admin tayinlash/olib tashlash | Yo‘q | Yo‘q | Ha |
| Arxivlash, global sozlamalar va eksport | Yo‘q | Yo‘q | Ha |

## 4. Status va vaqt qoidalari

- Kvest: `scheduled` → `active` → `completed`; superadmin `archived` holatiga o‘tkazishi yoki arxivdan chiqarishi mumkin.
- Barcha vaqtlar foydalanuvchidan `Asia/Tashkent` bo‘yicha olinadi; bazada UTC ISO-8601 shaklida saqlanadi.
- Umumiy vaqt va bosqich vaqti daqiqada beriladi; `0` — limit yo‘q.
- Jadval rejimidagi bosqich vaqtlari qat’iy o‘sib borishi va, umumiy limit o‘rnatilgan bo‘lsa, kvest yakunidan oshmasligi kerak.
- Jadvaldagi navbatdagi bosqich vaqti kelganda, ochiq qolgan oldingi bosqich qatnashchi uchun yopiladi.

## 5. Texnik tuzilma

- `main.py` — botni ishga tushirish, routerlar va scheduler.
- `quest_bot/handlers/` — umumiy menyu, kvest yaratish, qatnashchi/reyting, admin va murojaat oqimlari.
- `quest_bot/database.py` — SQLite jadval va tranzaksiyalar.
- `quest_bot/scheduler.py` — start vaqti, jadvaldagi bosqichlar va timeout nazorati.
- `quest_bot/services.py` — savol jo‘natish, guruh e’loni va chat a’zolarini chiqarish.
- `quest_bot/localization.py` — uz/ru/en tarjimalari.
- `tests/` — ma’lumotlar qatlami va asosiy qoidalar testlari.

## 6. Telegram cheklovlari va xavfsizlik eslatmalari

- Telegram Bot API chatdagi barcha eski a’zolar ro‘yxatini olish imkonini bermaydi. Bot faqat ro‘yxatga olingandan keyin kelgan `chat_member` hodisalaridan bilgan user ID’larni ko‘ra oladi; oldindan mavjud a’zolarni avtomatik ommaviy chiqarish mumkin emas. Eski a’zolarni ham boshqarish kerak bo‘lsa, user ID’lar ro‘yxatini alohida import qilish yoki qo‘lda whitelist/ro‘yxat tuzish kerak.
- A’zoni chiqarish uchun bot guruh/kanalda admin va `can_restrict_members` huquqiga ega bo‘lishi zarur. Kanalda e’lon yuborish uchun `can_post_messages`, taklif linki uchun `can_invite_users` talab qilinadi.
- Bir kishilik Telegram chat invite linkini olgan odam uni boshqasiga uzatishi mumkin; `member_limit=1` faqat birinchi muvaffaqiyatli qo‘shilishni kafolatlaydi. Link maqsadli odamga private yuborilishi lozim.
- Private kvest deep-linki umumiy taklifdir; u alohida chat invite linkidan mustaqil.
- SQLite faylini production muhitida doimiy disk/volume’da saqlash va muntazam zaxiralash kerak. Export arxivida kirish tokenlari va maxfiy invite URL’lar chiqarilmaydi.

## 7. Birinchi relizga kirmaydigan ishlar

- Web panel, reklama, guruhga ochiq javob yozish.
- Telegram API cheklovi sabab kanal a’zolarini avtomatik ro‘yxatlash.
- Kvest matnlarini foydalanuvchi tanlagan tilga avtomatik tarjima qilish.
- Bot ichidagi superadmin global ban interfeysi; hozirgi bloklash kvest doirasida amalga oshadi.
