# Geliştirme Kılavuzu

## Ortam

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python scripts\run_tests.py
```

macOS/Linux için `.venv/bin/python` kullanın. Kodlar işletim sisteminden bağımsızdır:

- Yollar `pathlib` ile kurulur.
- Dosyalar UTF-8 olarak açılır.
- Tarihler saat dilimi içeren UTC değerlerdir.
- Platforma özgü tek kod bloğu `infrastructure/file_lock.py` içindeki kilit çağrısıdır (`msvcrt`/`fcntl`, ikisi de standart kütüphane).

Genel kurallar:

- Bağımlılık eklerken `pyproject.toml` (aralık) ve `requirements*.txt` (sabit sürüm) birlikte güncellenir.
- `contracts/` altındaki dosyalar collector reposuyla aynı kalmalıdır.

## Yeni collector nasıl eklenir?

Monitor tarafında kod değişikliği gerekmez. Yeni kaynak, `social-data-collector` reposunda adaptör olarak eklenir ve sözleşme v1'e uygun gönderiler üretir; `source` alanı yeni platformun adıdır (ör. `x`). Monitor, `source` değerini serbest metin olarak kabul eder (desen: `^[a-z][a-z0-9_]{1,31}$`) ve sağlık durumunu kaynak bazında otomatik izler.

## Yeni classifier nasıl eklenir?

1. `application/ports.py` içindeki `Classifier` sözleşmesini uygulayan bir sınıf yazın:
   ```python
   class MyClassifier:
       classifier_type = "my_classifier"
       def classify(self, post: Post, analyzed_at: datetime) -> AnalysisResult: ...
   ```
2. `confidence` değerini metodolojisine uygun doldurun ve `confidence_method` alanında açıkça belirtin (ör. `heuristic_rule_score`, `calibrated_probability`).
3. `container.build_classifier` içinde yeni sınıfı bir `MONITOR_CLASSIFIER` değerine bağlayın ve `config.Settings.classifier` için izin verilen değerleri güncelleyin.
4. Sınıflandırıcının kendi unit testlerini ekleyin. Spesifikasyondaki örnek cümleler `tests/unit/test_classifier.py` içinde bulunur.

**LLM entegrasyonu:** `classification/llm.py` içindeki `LLMClient` protokolünü uygulayan, sağlayıcıya özgü bir adaptör yazılır ve `LLMClassifier(client)` olarak bağlanır. Gerçek gönderilerin harici bir servise gönderilmesi ayrı bir hukuki ve güvenlik onayı gerektirir; bu nedenle adaptör varsayılan olarak devre dışıdır. İstemci yokken sahte sonuç üretilmez.

**Kural genişletme:** Yeni anahtar kelimeler `classification/rules_tr.json` dosyasına eklenir. Yeni bir kategori gerekiyorsa önce `domain/enums.py` içindeki `ServiceCategory` veya `IssueCategory` listesine eklenir, ardından kural dosyasında kullanılır. Kurallar dosya yüklenirken doğrulanır; bilinmeyen kategori `ConfigurationError` üretir. Kural dosyası değiştiğinde `rules_version` artırılır.

- **İtibar sinyalleri:** `rules_tr.json` → `reputation_signals` (`solvency_rumor`, `deposit_flight`, `run_call`). Her yeni kalıp için `tests/unit/test_reputation.py` içine olumlu **ve** olumsuz örnek ekleyin (ör. "ATM'den paramı çektim" sinyal değildir).
- **İller:** `classification/locations_tr.json`. Gündelik bir kelimeyle karışabilen il adları `"strict": true` ile işaretlenir.
- **Konu terimleri:** Konu olarak listelenmemesi gereken genel kelimeler `classification/stopwords_tr.json` dosyasına eklenir.
- **Etkiyi ölçme:** Kural değişikliğinden önce ve sonra `export-labels` ile alınan etiketlerle `evaluate --labels ... [--rules yeni_kurallar.json]` çalıştırın. Tarafsız sonuç için `--sample review_random` kullanın. Geçmişe uygulamak için `reanalyze` çalıştırın.

**Etiket anlamları:** `confirm` sınıflandırmanın tüm alanlarının (şikâyet mi, hizmet, sorun, itibar sinyali) doğru olduğu anlamına gelir. `complaint` etiketinde hizmet ve sorun zorunludur. Etiketin nerede verildiği (`sample`) saklanır; yalnızca `review_random` tarafsız bir örneklemdir.

## Yeni storage adaptörü nasıl eklenir?

1. `application/ports.py` içindeki repository protokollerini uygulayın: `PostRepository`, `AnalysisRepository`, `IncidentRepository`, `AlertRepository`, `CollectorRunRepository` ve isteğe bağlı olarak `StorageDiagnostics`.
2. Sözleşmeyi koruyun:
   - `add_if_absent` ve `CollectorRunRepository.add` idempotent olmalıdır.
   - `IncidentRepository.save` ve `AlertRepository.save` `version` alanı için iyimser eşzamanlılık kontrolü yapmalıdır: beklenen sürüm `mevcut + 1` değilse `StorageError` fırlatılır.
   - Liste yöntemleri belgelendiği sırayla dönmelidir.
3. Altyapı hatalarını `application.errors.StorageError` tipine çevirin; API bu hatayı `503` olarak döndürür.
4. Yalnızca `container.py` dosyasında JSONL repository'leri yerine yeni adaptörü oluşturun. Application ve API katmanları değişmez.
5. `tests/unit/test_repositories.py` dosyasındaki senaryoları yeni adaptör için de çalıştırın.

PostgreSQL gibi çok süreçli bir depolamaya geçildiğinde `MonitoringService` içindeki süreç içi kilit, veritabanı işlemleri (transaction) ile desteklenmelidir.

## Yeni incident kuralı nasıl oluşturulur?

1. `application/detection/rules.py` içindeki `DetectionRule` protokolünü uygulayın:
   ```python
   class MyRule:
       name = "my_rule"
       def evaluate(self, context: RuleContext) -> RuleOutcome | None: ...
   ```
   `RuleContext` grubun anahtarını (`key`), değerlendirme zamanını (`now`) ve pencere istatistiklerini (`stats_by_window`) taşır.
2. Kural `RuleOutcome(level, window_minutes, complaints_count, unique_users_count, reason, details)` döndürür; söyleyecek bir şeyi yoksa `None`. `reason` API ve loglar için İngilizce tek satırdır; `details` yapılandırılmış gerekçedir ve dashboard Türkçe metni bundan üretir (`dashboard/labels_tr.py` → `detection_text`).
3. `container.py` içinde ilgili `DetectionPipeline` kural listesine ekleyin. Farklı bir gruplama gerekiyorsa (ör. itibar sinyalleri) yeni bir aggregator ile ayrı bir hat kurun. Motor her grup için en yüksek seviyeyi seçer.
4. Geçmiş veriye dayanan kurallar (ör. `BaselineRule`) yeterli geçmiş yokken seviye uydurmamalı, `None` döndürmelidir.

## Yeni notification provider nasıl eklenir?

1. `Notifier` protokolünü uygulayın (`name` özelliği ve `notify(alert)` yöntemi). Örnek: `infrastructure/notifiers.py` içindeki `LogNotifier`.
2. `container.build_notifiers` listesine ekleyin. Gerekli ayarları (`MONITOR_TEAMS_WEBHOOK_URL` vb.) `config.Settings` modeline ekleyin.
3. Alarm servisi, notifier'dan gelen istisnaları yakalar ve loglar; alarm kaydı diğer kanallardan bağımsız olarak saklanır. Ağ çağrısı yapan bir provider kendi zaman aşımını ve yeniden deneme politikasını uygulamalıdır.
4. Tekrar önleme alarm servisinde yapılır: aynı incident ve seviye için yalnızca bir kez `notify` çağrılır.

## Kod kuralları

- İsimlendirme İngilizce, tip ipuçları zorunlu.
- HTTP katmanında iş mantığı bulunmaz.
- Loglarda gönderi metni ve gizli değerler yer almaz.
- Yeni dosya yolu işlemlerinde `pathlib` ve `encoding="utf-8"` kullanılır.
- Testlerde geçici veri `tmp_path` ile oluşturulur; repoda kalıcı klasör oluşturulmaz.
