# Asistan Spor Verisi

25 Kasım 2026: Android artık GitHub veri dosyası okumaz. Merkezi yayın `asistan-8a2cf` Firestore projesindedir. `sports_feed/current_v2` tüm kategoriler için tek uzak fikstürdür; `sports_internal/sources` kaynakların son geçerli verisini tutar. Bir defalık GitHub aktarımının özgün alanları `source_archives` altında korunmuştur. Android paketindeki dosyalar yalnızca çevrimdışı başlangıç içeriğidir.

İş akışı yalnızca bu deponun `main` dalına bağlı Workload Identity Federation ile 10 dakikalık erişim belirteci alır. Kalıcı servis hesabı anahtarı kullanılmaz. Özel yayıncı rolü Firestore okuma/ekleme/güncelleme içerir; silme ve proje yönetimi içermez. Proje, sağlayıcı ve servis hesabı adları iş akışındaki herkese açık yapılandırmadır. Fikstür verisi GitHub'a commit edilmez.

Ortak kataloglar `app_content`, ortak küçük görseller `public_media` altında tutulur. Kişisel veri sürümleri `users/{uid}/personal_versions`, etkin sürüm `users/{uid}/state/current` altındadır. Yayıncı kişisel veri üretmez; Android kişisel içeriği doğrulanmış hesabıyla şifreli olarak eşitler.

Android Asistan, fikstürü Firebase Spark planındaki Firestore'dan alır. Bu depo
toplayıcı kodunu ve tarihsel GitHub fikstürünü içerir. Yeni akış güncel fikstürü
ve sağlayıcılardan gelen yanıtları açık depoya yazmaz.

## Kaynaklar

| Kapsam | Kaynak |
| --- | --- |
| Beşiktaş futbol, Türkiye, Türk takımlarının Avrupa maçları ve seçili Şampiyonlar Ligi maçları | Fixtur.es takım ve lig takvimleri, ayrıca Beşiktaş için Türkiye Kupası ve Süper Kupa takvimleri. |
| Beşiktaş erkek basketbol ve Merkezefendi Basket | Türk Telekom Basketbol'un yayımladığı BSL fikstürü. Saat 00.00 olarak yer alan eşleşmeler geçici program olarak işaretlenir. |
| Beşiktaş EuroLeague | EuroLeague'in yayımladığı sezon programı. |
| Öne çıkan NBA maçları | Fixtur.es NBA takvimi. |
| F1 ve MotoGP | Jolpica F1 ve Nixxo MotoGP takvimi. Jolpica'nın ücretsiz kullanımı ticari olmayan projeler içindir. |
| UFC | UFC Calendar. Ana maçın iki sporcusu, duyurulan eşleşmeyle doğrulanır. |
| Denizli futbol | Denizli ASKF Denizlispor A takım fikstürü. Tarihi duyurulan iç saha maçları. |
| Denizli voleybol | TVF Kadınlar 1. Ligi fikstürü. Denizli Büyükşehir Belediyespor’un Denizli iç saha maçları. |
| Denizli konser ve kültür | Denizli Büyükşehir Belediyesi ve iTicket. Önümüzdeki 30 gün. |
| Boks | WBA dünya kemeri ve WBO şampiyonluk takvimleri, organizatörce doğrulanmış WBC ana maçları. |
| Öne çıkan boks ve Türkiye'deki dövüş geceleri | Organizatör, yerel federasyon ve yetkili bilet sayfasıyla doğrulanan seçki. Kayıtlar data/featured_combat_events.json dosyasında tutulur. |
| Core CFC ve Vendetta Fight Nights | Türkiye MMA organizasyon listesinde Türkiye konumu ve başlangıcı doğrulanan yeni kayıtlar. |
| Türkiye A Erkek Millî Basketbol | FIBA 2027 Dünya Kupası Avrupa Elemeleri resmi maç listesi. |
| Türkiye Kadın A Millî Voleybol | FIVB Dünya Kupası grup duyurusu ve TVF bağlantısı. Duyurulan tarih aralığı turnuva kaydı olarak işlenir. |

Boks akışının yayımlanan görünümünde yalnızca erkek karşılaşması olduğu doğrulanmış kimlikler kullanılır. Kimlikler `data/reviewed_mens_boxing.json` dosyasında tutulur. UFC kayıtları kendi kaynağından alınır.

## Yerel doğrulama

```text
python -m unittest discover -s tests
python -m scripts.publish_fixtures --preview fixtures-preview.json
```

Önizleme Firestore'a yazmaz. Python 3.12 önerilir.

## Spark üzerinde etkinleştirme

1. Firebase projesinin **Spark** planında olduğunu doğrulayın. Varsayılan
   Firestore veritabanını ve Firebase Authentication'da anonim giriş sağlayıcısını
   etkinleştirin. Cloud Functions veya faturalandırma gerekmez.
2. Mevcut Firestore kurallarına bu depodaki `/sports_feed/current` ve `/sports_feed/current_v2` erişim kurallarını
   ekleyin. Projenin diğer koleksiyonlarına ait mevcut kuralları koruyun.
3. Google Cloud'da bu deponun ana dalıyla sınırlı bir Workload Identity
   Federation sağlayıcısı ve yalnızca fikstür işi için bir servis hesabı kurun.
   Hesaba Firestore belge okuma ve yazma yetkisini verin. Kalıcı servis hesabı
   anahtarı oluşturmak gerekmez.
4. İş akışında projenin herkese açık WIF yapılandırması hazırdır. `main` dalına
   yayıncı kodu gönderildiğinde veya **Run workflow** ile çalıştırıldığında iki
   fikstür belgesinin yazıldığını doğrulayın. Gizli anahtar/depo değişkeni gerekmez.

İş akışı herkese açık depoda standart GitHub çalıştırıcısını kullanır. Varsayılan
kontrol aralığı üç saattir. Bir kaynak hata verirse onun son başarılı kayıtları
korunur. Android yenileme düğmesi yayımlanmış son Firestore fikstürünü okur. Eski ve yeni uygulama sürümlerinin belgeleri birlikte güncellenir.

GitHub, 60 gün depo etkinliği olmayan açık depolarda zamanlanmış iş akışlarını
durdurabilir. Bu durumda Actions sayfasında iş akışını yeniden etkinleştirin ve
elle bir güncelleme çalıştırın.

