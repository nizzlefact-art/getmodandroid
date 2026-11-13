GetModAndroid (Python / Flask / SQLite) - run.bat se chalao
=======================================================
1. Python 3.10+ install karo (python.org) - installer me "Add Python to PATH" tick karna.
2. Zip ko extract karo, run.bat pe double-click.
   (Pehli baar packages install hote hain - internet chahiye.)
3. Browser khulega: http://127.0.0.1:5000/
4. Pehli baar http://127.0.0.1:5000/admin/ kholo -> admin email/password banao (10+ characters).
5. Admin me: Apps/Games -> Add -> Published -> "Versions" me APK upload.

Data kahan save hota hai:
  data/packnest.db   - database (backup ke liye is file ko copy kar lo)
  storage/apks/      - uploaded APK files
  uploads/           - icons, screenshots, images
Phone se test karna ho: app.py me HOST = "0.0.0.0" kar do aur PC ka IP use karo.

Sirf wahi APK upload karo jinka distribute karne ka tumhe haq/permission hai.
