# МХБ Дохионы Самбар — GitHub Pages хувилбар

Энэ багц нь Claude-ийн `window.claude.use("db")` хамаарлыг арилгасан.

## Repo-д байх файлууд

- `index.html` — таны одоогийн дизайн, GitHub Pages дээр `data.json`-оос шинэ дата уншина.
- `data.json` — одоогийн бүх seed дата.
- `scripts/update_mse.py` — MSE-ийн `https://new.mse.mn/todays-trade` хуудсыг Chromium/Playwright-аар уншиж шинэ өдрийг нэмнэ.
- `.github/workflows/update.yml` — өдөр бүр Улаанбаатарын 18:00 цагт ажиллана.
- `requirements.txt`

## GitHub дээр хийх 3 алхам

1. `munkhamgalanbatulzii-ui/mse-dashboard` repo-ийн root-д энэ багцын бүх файлыг upload/replace хийнэ.
2. `Settings → Pages → Build and deployment → Deploy from a branch → main → /(root) → Save`.
3. `Actions → Update MSE Dashboard → Run workflow` нэг удаа гараар ажиллуулж шалгана.

PAT / GH_TOKEN / Claude GitHub connector шаардлагагүй.
Workflow өөрийн GitHub `GITHUB_TOKEN`-оор `data.json`-оо commit хийнэ.

## Анхаарах зүйл

MSE `new.mse.mn` нь client-side хүснэгт ашигладаг тул updater Playwright/Chromium ашиглана.
Хэрэв MSE хүснэгтийн баганын бүтэц өөрчлөгдвөл workflow алдаатай зогсож, хуучин `data.json`-г эвдэхгүй.
