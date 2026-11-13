from app import app, HOST, PORT, MAX_APK_MB
print(f"GetModAndroid chal raha hai: http://{HOST}:{PORT}/   |   Admin: http://{HOST}:{PORT}/admin/")
try:
    from waitress import serve
    serve(app, host=HOST, port=PORT, threads=8, max_request_body_size=(MAX_APK_MB + 20) * 1048576)
except ImportError:
    app.run(HOST, PORT)
