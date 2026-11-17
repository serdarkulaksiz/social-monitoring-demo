# Windows Kurulumu (kısıtlı ağ dahil)

Bu proje Python 3.13 ile çalışır ve bağımlılıklarını kendi sanal ortamına kurar. Yönetici yetkisi gerekmez.

## 1. Python

Python 3.13 (64-bit) kurulu olmalıdır. Kontrol etmek için:

```powershell
py -3.13 --version
```

`py` başlatıcısı yoksa `python --version` komutuyla sürümü kontrol edin ve aşağıdaki komutlarda `py -3.13` yerine `python` yazın.

## 2. İnternet erişimi olan ortamda kurulum

```powershell
cd social-incident-monitor
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e . --no-deps
copy .env.example .env
notepad .env
.venv\Scripts\python -m social_incident_monitor check-config
```

Kurumsal proxy arkasında `pip` için:

```powershell
$env:HTTPS_PROXY = "http://proxy.kurum.local:8080"
.venv\Scripts\python -m pip install --proxy $env:HTTPS_PROXY -r requirements.txt
```

Kurumsal bir TLS denetim (SSL inspection) sertifikası kullanılıyorsa, sertifika doğrulamasını kapatmak yerine kök sertifikayı `pip` ayarına ekleyin:

```powershell
.venv\Scripts\python -m pip config --site set global.cert C:\certs\corporate-root-ca.pem
```

## 3. İnternet erişimi olmayan ortamda kurulum (wheelhouse)

**Bağlantılı bir makinede**, tercihen aynı Windows sürümü ve Python 3.13 ile:

```powershell
py -3.13 -m pip download -r requirements.txt -d wheelhouse
py -3.13 -m pip download setuptools wheel -d wheelhouse
```

Bağlantılı makine Windows değilse platform açıkça belirtilmelidir:

```bash
python3.13 -m pip download -r requirements.txt -d wheelhouse --platform win_amd64 --python-version 3.13 --implementation cp --only-binary=:all:
python3.13 -m pip download setuptools wheel colorama==0.4.6 -d wheelhouse --only-binary=:all:
```

Bu durumda `sys_platform == "win32"` koşullu paketler (ör. `colorama`, yalnızca geliştirme bağımlılıklarında) host platformuna göre değerlendirildiği için ayrıca indirilmelidir.

`wheelhouse` klasörünü kurumun onaylı aktarım yöntemiyle hedef makineye taşıyın. Bu klasör `.gitignore` kapsamındadır ve repoya eklenmemelidir.

**Hedef makinede:**

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install --no-index --find-links wheelhouse -r requirements.txt
.venv\Scripts\python -m pip install --no-index --find-links wheelhouse -e . --no-deps
```

Geliştirme/test bağımlılıkları için `requirements-dev.txt` dosyasıyla aynı adımlar uygulanır.

## 4. Doğrulama

```powershell
.venv\Scripts\python -m social_incident_monitor check-config
.venv\Scripts\python scripts\run_tests.py
```

`run_tests.py` içindeki testler internet erişimi gerektirmez.

## 5. Sürekli çalıştırma

Uygulama bir konsol süreci olarak çalışır. Windows hizmeti olarak çalıştırmak için kurumun standart hizmet sarmalayıcısı veya Görev Zamanlayıcı (oturum açılmasa da çalış, başlangıçta tetikle) kullanılabilir. Çalışma dizini proje klasörü, komut `.venv\Scripts\python.exe -m social_incident_monitor ...` olmalıdır. Hizmet hesabının `data` klasörüne yazma izni bulunmalıdır.
