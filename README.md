# Asistan Firebase spor yayıncısı

26 Kasım 2026: Spor verisinin toplanması ve yayımlanması Firebase sunucusuna taşındı. Uygulamanın çalışması veya veri güncellemesi için GitHub gerekmez. Bu depo yalnız kaynak kodunu, testleri ve sürüm geçmişini tutar. GitHub Actions yalnız test çalıştırır; veri toplamaz, Firebase'e bağlanmaz ve zamanlanmış işi yoktur.

## Çalışan altyapı

- Proje: `asistan-8a2cf`, bölge: `europe-west3`, Python 3.13.
- Firebase işlevi: `refresh_sports`; tetikleyici: Cloud Scheduler.
- Zamanlama: `10 */3 * * *`, saat dilimi `Europe/Istanbul` (00:10, 03:10, 06:10, 09:10, 12:10, 15:10, 18:10, 21:10).
- 512 MiB, 0,3333 CPU, en fazla 1 örnek/1 eşzamanlı istek, en az 0 örnek, 240 saniye zaman aşımı, zamanlayıcı otomatik tekrarı 0.
- Servis kimliği: `asistan-sports-runtime`; Google tarafından sağlanan çalışma kimliği kullanılır. Kalıcı anahtar veya GitHub erişim belirteci yoktur. İşlev herkese açık çağrıya izin vermez.
- Eski GitHub WIF sağlayıcısı ve yayıncı servis hesabı kapatıldı; eski yayıncının Firestore proje rolü kaldırıldı.
- İşlev derleme görüntüleri için 1 günlük Artifact Registry temizleme politikası vardır. Bu politika uygulama verisini silmez.

## Veri kaynakları

Futbol ve NBA takvimleri Fixtur.es; BSL ve EuroLeague resmî/kamuya açık fikstürler; millî basketbol/voleybol FIBA/TVF duyuruları; Denizli ASKF/TVF/belediye/bilet kaynakları; boks WBC/WBA/WBO ve doğrulanmış organizatör seçkileridir.

UFC doğrudan `https://www.ufc.com/events` ve resmî etkinlik kartlarından okunur. Ana kartın Unix zamanı kullanılır; ana maçın tam adları kart başlığıyla karşılaştırılır. MotoGP doğrudan `https://www.motogp.com/en/calendar/{year}` üzerinden okunur. Genel takvim penceresinin 08:00/18:00 gibi açılış-kapanış saatleri yarış saati sayılmaz; doğrulanmış seans saati yoksa kayıt tarih olarak yayımlanır. F1 Jolpica ve Sportstimes kaynağını kullanır; ücretsiz Jolpica kullanımının ticari olmayan kullanım şartı korunur.

Kaynak hata verirse son başarılı kayıtları korunur. Geçmiş kaynak/arşiv metinlerinde eski adreslerin bulunması, bunlara yeniden bağlanıldığı anlamına gelmez. Çalışan kaynaklar GitHub/GitHub Pages takvimi kullanmaz.

## Firebase belgeleri

- `sports_feed/current_v2`: Android'in güncel ortak fikstürü.
- `sports_feed/current`: eski istemci uyum akışı.
- `sports_internal/sources`: kaynak başına son başarılı veri ve kontrol zamanı.
- `sports_internal/editorial`: boks doğrulama ve seçki listeleri; depoda veri JSON'u yoktur.
- `source_archives`: ilk geçişin tarihsel kopyaları; çalışma sırasında Git geçmişi okunmaz.

Kişisel veriler bu yayıncı tarafından üretilmez. Android bunları kullanıcı hesabında şifreli sürümlerle ayrı yönetir. Yayıncı rolü proje düzeyinde Firestore okuma/ekleme/güncelleme içerir; koleksiyon bazlı IAM izolasyonu ayrıca değerlendirilmelidir.

## Test ve dağıtım

```text
python -m unittest discover -s tests
```

Dağıtım ana Android projesinin `firebase.json` dosyasından, resmî Firebase CLI ile yerel kaynak yüklemesi olarak yapılır. GitHub üzerinden otomatik dağıtım yoktur. Yerel `venv`, `.git`, testler, veri dosyaları ve günlükler Firebase dağıtım paketine dahil edilmez. Eski yerel dosya üreticisi ve GitHub içe aktarma yolu kapatılmıştır.

Bu altyapı Blaze gerektirir; ücretsiz kotalar aşılırsa ücret oluşabilir. Kaynak sınırları ve üç saatlik zamanlama maliyet azaltır, kesin parasal tavan sağlamaz. APK'yı yenilemek kullanıcı kurulumudur; veri güncellemesi Firebase zamanlayıcısından otomatik yapılır.

Önceki Git commit'lerindeki tarihsel veri dosyaları yeniden yazılmadı. Bunlar uygulama/güncelleyici bağımlılığı değildir; eski geçmişin tamamen silinmesi ayrı bir işlemdir.
