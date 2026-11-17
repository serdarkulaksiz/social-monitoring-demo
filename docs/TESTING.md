# Test

## Testleri çalıştırma

Standart ve önerilen yol:

```
python scripts/run_tests.py
```

Ek pytest argümanları aynen aktarılır:

```
python scripts/run_tests.py -v
python scripts/run_tests.py tests/unit -k incident
python scripts/run_tests.py --check-only     # yalnızca .pytest_cache kontrolü
```

`run_tests.py`:

1. Projenin `.venv` yorumlayıcısı varsa onu, yoksa çağıran yorumlayıcıyı kullanır.
2. Test öncesinde `.pytest_cache` varsa testi başlatmadan hata verir (çıkış kodu 3).
3. `PYTHONDONTWRITEBYTECODE=1` ayarlar ve `python -B -m pytest -p no:cacheprovider` çalıştırır.
4. Test sonrasında `.pytest_cache` veya yeni `__pycache__`/`.pyc` oluştuysa başarısız olur (çıkış kodu 3).
5. Hiçbir dosyayı silmez. Önleme, dosyaların oluşmasını engelleyerek yapılır.

## Cache üretmeyen yapılandırma

- `pytest.ini` içinde `addopts = -p no:cacheprovider` bulunur. Böylece doğrudan `pytest` çağrısı da `.pytest_cache` oluşturmaz.
- `tests/unit/test_pytest_config.py` bu ayarı, eklentinin yüklenmediğini ve test çalıştırıcısının bayraklarını doğrular.
- Doğrudan `pytest` çağrısı bytecode üretimini engellemez (pytest assertion rewriting `__pycache__` yazabilir). Bytecode üretmemek için her zaman `scripts/run_tests.py` kullanın.
- GitHub Actions iş akışı (`.github/workflows/ci.yml`) testten önce ve sonra `.pytest_cache` kontrolü yapar.

## Test türleri

| Klasör | Kapsam |
|---|---|
| `tests/unit` | Normalizasyon, sınıflandırma (spesifikasyon örnekleri dahil), itibar sözlüğü (olumlu/olumsuz örnekler), il eşleştirme ("karşı", "müşteri", "ağrı" yanlış eşleşmez), konu terimleri ve yükselen konular, Poisson olasılığı, normal seviye profili (kırpılmış ortalama, yetersiz geçmiş, hızlı artış), tespit hatları, itibar sayımı (kişi + yayılım), etiketlerin sınıflandırıcıyı ezmesi ve kalite metrikleri, denetim kaydı hash zinciri ve kurcalama tespiti, JSONL dayanıklılığı, repository'ler, incident yaşam döngüsü, alarm üretimi, collector sağlığı, konfigürasyon, dosya kilidi, sözleşme uyumu (v1.0 ve v1.1), repo hijyen aracı; metin modeli: özellikler (kullanıcı adı maskesi, olumsuzluk, harf parçaları), Naive Bayes ve lojistik regresyon, tekrarlanabilir eğitim, model dosyası sağlama toplamı, kural + model birleşiminin kuralları (kurtarma, veto, kategori, gölge mod, bozuk model), CSV içe aktarma ve hatalı satırlar, deterministik ve ayrık eğitim/test bölmesi, kalite kapısı; dolandırıcılık kategorisi ve Ziraat Katılım ayrımı |
| `tests/integration` | REST API → uygulama → JSONL: kimlik doğrulama, batch kabulü, kullanıcı adlarının saklanması, etkileşim güncellemesi (applied/stale/unknown, gelecek tarih), öğe bazlı reddetme, hata kodları, konular ve iller; dashboard: tüm sayfalar, Türkçe metinler, CSP/başlıklar, XSS kaçışlama ve `javascript:` linklerin düşürülmesi, Markdown rapor, işlem korumaları (yerel/api_key/off modları, eksik başlık, Origin, Sec-Fetch-Site), bilinmeyen host adı |
| `tests/e2e` | Sentetik gönderiler → WARNING → CRITICAL → alarm; yeniden başlatma; otomatik kapanış; onay/kapama; collector sağlık alarmı; denetim kaydı, kanıtlar, rapor, süreler ve `verify-audit` (kurcalanmış kayıtta çıkış kodu 4); düşük hacimde normal seviye kuralının olay açması; yanlış alarm etiketinin sayıyı düşürüp olayı kapatması; `export-labels`, `evaluate`, `reanalyze`; itibar sinyalinin yayılımla yükselmesi ve analist etiketiyle düşmesi; metin modeli döngüsü (`import-dataset` → `split-dataset` → `train-model` → `evaluate` → `promote-model`, kötü modelin reddi, geri alma), gölge modda sunucu, ayrışma kuyruğu ve kalite sayfası |

Tüm testler internet erişimi olmadan çalışır. Zaman `FakeClock` ile kontrol edilir; testler gerçek süre beklemez.

## Projeler arası uçtan uca kontrol

İki repo yan yana klonlandığında ve her birinin `.venv` ortamı kurulduğunda:

```
python scripts/cross_project_e2e.py --collector-dir ../social-data-collector
```

Betik:

1. Monitor'ü sentetik modda, geçici bir veri klasörü ve boş bir port ile ayrı bir süreç olarak başlatır.
2. Collector'ı `run-once` ve sentetik kaynak ile ayrı bir süreç olarak çalıştırır.
3. HTTP üzerinden CRITICAL alarmı, incident tekilliğini, collector raporunu, sağlık durumunu, kullanıcı adlarının saklandığını, etkileşim sayılarını, ayrı itibar sinyalini ve yükselen konuyu doğrular (11 kontrol).

Tüm geçici veriler işletim sisteminin geçici klasöründe oluşturulur ve silinir. Bu kontrol pytest paketinin parçası değildir.

## Test verileri

- API testleri `tests/conftest.py` içindeki `post_payload()` ve `batch_payload()` yardımcılarıyla sentetik gönderi üretir.
- Sözleşme örnekleri `contracts/examples/` altındadır.
- Gerçek sosyal medya verisi test verisi olarak kullanılmaz ve repoya eklenmez.

## Sonuçların yorumlanması

- `N passed` ve ardından `[run_tests] Cache check passed` satırı başarılı sonucu gösterir.
- `[run_tests] FAILED: .pytest_cache exists before the test run.` mesajı, klasörün daha önce başka bir araçla oluşturulduğunu gösterir. Klasörü kimin oluşturduğunu araştırın; araç onu silmez.
- `FAILED: bytecode files were created during the test run.` mesajı, bir testin veya aracın `-B` ayarını aşan bir alt süreç başlattığını gösterir.
- Çıkış kodu 3 cache kontrolü hatası, diğer sıfırdan farklı kodlar pytest sonucudur.
