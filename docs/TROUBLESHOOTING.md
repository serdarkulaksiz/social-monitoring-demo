# Sorun Giderme

## Uygulama başlamıyor

**`Configuration error: invalid configuration: MONITOR_API_KEY: Field required`**
`.env` dosyası bulunamadı veya anahtar tanımlı değil. Komutu `.env` dosyasının bulunduğu klasörde çalıştırın ya da `--env-file` ile yolu verin. Anahtar en az 24 karakter olmalıdır.

**`MONITOR_FOO: unknown setting`**
`MONITOR_` önekli değişkenin adı yanlış yazılmış. Geçerli adlar için `docs/CONFIGURATION.md` dosyasına bakın. Windows'ta kalıcı ortam değişkenlerini `set MONITOR` (cmd) veya `Get-ChildItem Env:MONITOR*` (PowerShell) ile listeleyebilirsiniz.

**`... monitor.lock is locked by another process; only one instance may use this data directory`**
Aynı veri klasörünü kullanan başka bir monitor süreci çalışıyor. İkinci örneği farklı bir `MONITOR_DATA_DIR` ile başlatın veya ilk süreci durdurun. Kilidi işletim sistemi tutar; süreç kapanınca kilit kendiliğinden kalkar. Lock dosyasını silmek gerekmez.

**`[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8000)`** (Windows) veya **`[Errno 48] Address already in use`** (macOS)
Port kullanımda. Portu kullanan süreci bulmak için Windows'ta `netstat -ano | findstr :8000`, macOS'ta `lsof -i :8000` kullanın. Ya o süreci durdurun ya da `MONITOR_PORT` değerini değiştirin.

**`allowed_hosts (MONITOR_ALLOWED_HOSTS) must list the server's host names ...`**
`MONITOR_HOST` loopback dışında bir adres (ör. `0.0.0.0`) ama sunucunun hangi adlarla çağrılacağı belirtilmemiş. `MONITOR_ALLOWED_HOSTS=monitor.banka.local,10.0.0.5` gibi bir değer verin.

**`MONITOR_CLASSIFIER=llm selected, but no LLM provider adapter is installed`**
LLM adaptörü bu sürümde bulunmaz. `MONITOR_CLASSIFIER=rule_based` kullanın.

## API erişim hataları

| Belirti | Neden | Çözüm |
|---|---|---|
| `401 unauthorized` | `X-API-Key` yok veya yanlış | Collector'daki `COLLECTOR_MONITOR_API_KEY` ile monitordaki `MONITOR_API_KEY` aynı olmalıdır. Sondaki boşluk ve tırnak karakterlerini kontrol edin. |
| `422 validation_error`, `loc: ["body","schema_version"]` | Sözleşme sürümü uyuşmuyor | Collector ve monitor aynı sözleşme sürümünü (`contracts/` SHA-256) kullanmalıdır. |
| Batch yanıtında `rejected` → `synthetic posts are not accepted` | Canlı örneğe sentetik veri gönderilmiş | Demo için ayrı bir veri klasörüyle `MONITOR_ACCEPT_SYNTHETIC=true` olan ayrı bir örnek çalıştırın. |
| `rejected` → `created_at is in the future` | Collector makinesinin saati ileride | Saati NTP ile eşitleyin veya geçici olarak `MONITOR_MAX_CLOCK_SKEW_SECONDS` değerini artırın. |
| `400 Invalid host header` | İstek, `MONITOR_ALLOWED_HOSTS` listesinde olmayan bir adla geldi | Tarayıcıda veya collector'da kullanılan adı listeye ekleyin. Varsayılan liste yalnızca `127.0.0.1` ve `localhost` içerir. |
| Dashboard'da "İşlem yapılamadı: dashboard actions are only accepted from this computer" | `MONITOR_DASHBOARD_ACTIONS=local` iken başka bir bilgisayardan işlem yapıldı | Dashboard'u monitor'ün çalıştığı bilgisayardan kullanın veya `api_key` moduna geçin. |
| Dashboard'da "missing X-Dashboard-Action header" / "cross-origin request refused" | İstek dashboard sayfası dışından geldi | İşlemleri dashboard üzerinden yapın; API kullanılacaksa `/api/v1/...` uç noktalarını `X-API-Key` ile çağırın. |
| `422`: `a 'complaint' label needs service_category and issue_category` | Şikâyet etiketi kategori olmadan gönderildi | Hizmet ve sorun seçin. Boş bırakılan alanın sınıflandırıcı tahminiyle doldurulması kalite ölçümünü yanıltacağı için zorunludur. |
| `503 storage_unavailable` | Disk dolu, izin yok veya dosya kilitli | Aşağıdaki "Dosya erişim hataları" bölümüne bakın. Collector bu durumda verileri kuyrukta tutar ve sonra yeniden gönderir. |

## Dosya erişim hataları

**`cannot write posts.jsonl: Permission denied`**
Servis hesabının `MONITOR_DATA_DIR` klasörüne yazma izni yok. Windows'ta izinleri `icacls data` ile kontrol edin ve gerekirse servis hesabına Modify izni verin. Klasörü OneDrive gibi senkronize edilen dizinlerde tutmayın; eşitleme istemcileri dosyaları geçici olarak kilitleyebilir.

**`cannot write ...: No space left on device`** / **`There is not enough space on the disk`**
Disk dolu. Yer açın ve isteğin tekrar gönderilmesini bekleyin. Collector reddedilen batch'leri yeniden gönderir.

**Log: `skipping corrupt line 1234 in posts.jsonl`** veya dashboard'da "Corrupt storage lines were skipped"
Dosyada geçersiz JSON veya şemaya uymayan bir satır var. Genellikle elektrik kesintisinde yarım kalan son satırdır. Uygulama satırı atlar ve çalışmaya devam eder. İncelemek için:

```
python -c "import json,sys; [print(n) for n,l in enumerate(open(sys.argv[1],encoding='utf-8'),1) if l.strip() and not l.lstrip().startswith('{')]" data/posts.jsonl
```

Elle düzeltme yapacaksanız önce servisi durdurun ve dosyanın yedeğini alın.

**`version conflict for inc-…`**
Aynı veri klasörü üzerinde beklenmeyen bir eşzamanlı yazma oldu (ör. dosya elle düzenlendi). Servisi yeniden başlatın; dosya en yüksek sürümden yeniden yüklenir.

**Dosyalar çok büyüdü**
Servis durdurulmuşken `python -m social_incident_monitor compact` komutunu çalıştırın. Bu komut incident ve alarm dosyalarını yalnızca son sürümlerle atomik olarak yeniden yazar. `posts.jsonl` ve `analysis.jsonl` dosyaları append-only olduğu için arşivleme kurumun saklama politikasına göre yapılmalıdır.

## Denetim kaydı ve tespit

**Log: `audit trail incident_events.jsonl failed verification`** veya dashboard'da "Kayıt bütünlüğü DOĞRULANAMADI"
Denetim kaydında bir kayıt değiştirilmiş, silinmiş, sırası bozulmuş veya okunamayan bir satır var. Servisi durdurup ayrıntıyı görün:

```
python -m social_incident_monitor verify-audit
```

Dosyayı düzeltmeye çalışmayın; kanıt niteliği taşır. Yedeklerle karşılaştırın ve bilgi güvenliği ekibine bildirin. Elektrik kesintisinde yarım kalan son satır da "unreadable line" olarak raporlanır.

**Dashboard'da "Normal seviye (baseline) kuralı için N günlük veri var"**
Kural en az `MONITOR_BASELINE_MIN_HISTORY_DAYS` (varsayılan 7) günlük veri ister. O zamana kadar sabit eşikler çalışır; bu bir hata değildir.

**Kural dosyası değişti ama eski gönderiler eski etiketleriyle görünüyor**
Sınıflandırma gönderi geldiğinde yapılır. Geçmişi yeni kurallarla yeniden sınıflandırmak için servisi durdurup `python -m social_incident_monitor reanalyze --since 14d` çalıştırın.

## Konfigürasyon hataları

- `MONITOR_DETECTION_THRESHOLDS: ... thresholds reference windows not listed`: Eşik için kullanılan pencere `MONITOR_DETECTION_WINDOWS_MINUTES` listesinde yok.
- `threshold '15:30:10' ...` / `0 < warning <= critical`: WARNING değeri CRITICAL değerinden büyük olamaz.
- Kural dosyası hatası (`invalid classification rules: 'teleportation' is not a valid ServiceCategory`): Kural dosyasında tanımsız bir kategori kullanılmış.

Konfigürasyonu servis başlatmadan doğrulamak için `python -m social_incident_monitor check-config` komutunu kullanın.

## Collector ve Monitor iletişim problemleri

1. Monitor ayakta mı? Collector makinesinden:
   ```
   python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read())"
   ```
2. Kimlik doğrulama: Collector logunda `monitor refused the API key (HTTP 401)` görünüyorsa anahtarlar farklıdır.
3. Proxy: Collector, monitor isteklerini varsayılan olarak proxy'siz gönderir (`COLLECTOR_MONITOR_USE_PROXY=false`). Monitor uzak bir sunucudaysa ve kurumsal proxy üzerinden erişiliyorsa bu değeri `true` yapın ve `NO_PROXY` listesini kontrol edin.
4. Dashboard'da collector `UNKNOWN`: Monitor henüz hiç çalışma raporu almamıştır. Collector çalışmıyor olabilir veya raporlar collector kuyruğunda bekliyor olabilir (`python -m social_data_collector status`).
5. Dashboard'da collector `STALE`: Son başarılı çalışmanın üzerinden `MONITOR_COLLECTOR_STALE_AFTER_MINUTES` dakikadan fazla geçmiştir. Collector durmuş, monitor'e ulaşamıyor veya kaynak platform erişilemez olabilir. Bu bir bankacılık arızası değildir; ayrı bir `COLLECTOR_HEALTH` alarmı olarak görünür.
6. Dashboard'da collector `DEGRADED`: Son çalışma başarısız veya kısmidir. Hata metni "Detail" sütununda görünür (ör. `HTTP 403 from Bluesky`).

## Bağlantı, proxy ve SSL sorunları

Monitor dış servislere bağlanmaz. Proxy ve SSL sorunları collector tarafında görülür; ayrıntılar için collector reposundaki `docs/TROUBLESHOOTING.md` dosyasına bakın. Monitor'e TLS ile erişiliyorsa sertifika yönetimi önündeki ters proxy tarafından yapılır.
