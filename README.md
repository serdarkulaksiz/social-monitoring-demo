# Social Monitoring – Demo

Bu repo tanıtım için hazırlanmış geçici bir kopyadır: `social-incident-monitor` (izleme uygulaması ve dashboard).

## Windows'ta çalıştırma

1. **Python 3.13** kurun (https://www.python.org/downloads/). Kurulumun ilk ekranında **"Add python.exe to PATH"** kutusunu işaretleyin.
2. Git Bash açıp şunları yazın:

   ```bash
   cd /c
   git clone https://github.com/serdarkulaksiz/social-monitoring-demo.git
   cd social-monitoring-demo
   explorer .
   ```

3. Açılan klasörde **`demo-baslat.bat`** dosyasına çift tıklayın. Windows uyarı verirse **Ek bilgi → Yine de çalıştır** deyin.
4. İlk açılışta kurulum birkaç dakika sürer. Sonra dashboard tarayıcıda açılır.

## Gönderi atma

Demo açıkken ikinci bir Git Bash penceresinde (anahtar gerekmez):

```bash
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ); curl -X POST http://127.0.0.1:8000/api/v1/posts/batch -H "Content-Type: application/json" -d '{"schema_version":"1.1","batch_id":"deneme-1","posts":[{"source":"synthetic","source_post_id":"1","author_id":"kisi1","text":"ziraat mobil yine gitti","created_at":"'$NOW'","collected_at":"'$NOW'"}]}'
```

Her gönderide `batch_id` ve `source_post_id` farklı olmalı. Alarm için 15 dakikada en az 10 farklı `author_id` gerekir. Port 8000 değilse demo penceresinde yazan adresi kullanın.

Tanıtım akışı ve sorun giderme: [docs/DEMO.md](docs/DEMO.md)

Demo verisi uydurmadır (sentetik).
