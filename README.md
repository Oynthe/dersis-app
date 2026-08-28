<p align="center">
  <img src="docs/dersis.png" alt="DERSİS logosu" width="260">
</p>

<h1 align="center">DERSİS</h1>

<p align="center">
  <b>Okullar ve üniversiteler için akıllı, tamamen çevrimdışı ders programı yazılımı.</b><br>
  <i>Ders Programı Hazırlama Sistemi · Class Schedule Preparation System</i>
</p>

<p align="center">
  <a href="https://github.com/Oynthe/dersis-app/releases/latest"><img src="https://img.shields.io/badge/%E2%AC%87%20%C4%B0ndir-Son%20S%C3%BCr%C3%BCm-2ea44f?style=for-the-badge" alt="İndir — Son Sürüm"></a>
</p>

<p align="center">
  <sub>Windows yükleyicisi · komut satırı için <a href="scripts/download_release.py"><code>scripts/download_release.py</code></a></sub>
</p>

---

<p align="center"><b>📖 Dilinizi seçin · Choose your language · Sprache wählen · Elija su idioma</b></p>

<p align="center">
  <a href="README-en.md"><img src="https://img.shields.io/badge/English-6e4f9e?style=for-the-badge" alt="English"></a>
  <a href="README-tr.md"><img src="https://img.shields.io/badge/T%C3%BCrk%C3%A7e-6e4f9e?style=for-the-badge" alt="Türkçe"></a>
  <a href="README-de.md"><img src="https://img.shields.io/badge/Deutsch-6e4f9e?style=for-the-badge" alt="Deutsch"></a>
  <a href="README-es.md"><img src="https://img.shields.io/badge/Espa%C3%B1ol-6e4f9e?style=for-the-badge" alt="Español"></a>
</p>

---

DERSİS, eğitim kurumları için **haftalık ders programları** oluşturur, eniyiler ve yönetir.
Tamamen kendi bilgisayarınızda çalışır — **hiçbir zaman giriş, hesap veya internet bağlantısı
gerektirmez** — ve otomatik çakışma önlemeyi, yapay zekâ destekli eniyilemeyi (sezgisel arama +
Büyük Komşuluk Araması + Google OR-Tools CP-SAT), açıklanabilir kararları, sürükle-bırak
düzenlemeyi, analizleri ve Excel/CSV/PDF dışa aktarmayı bir araya getirir.

Tüm belgeler — özellikler, kurulum, kaynaktan derleme, kullanım kılavuzu ve hata bildirimi —
yukarıdaki her dilde mevcuttur:

- 🇬🇧 **English** → [`README-en.md`](README-en.md)
- 🇹🇷 **Türkçe** → [`README-tr.md`](README-tr.md)
- 🇩🇪 **Deutsch** → [`README-de.md`](README-de.md)
- 🇪🇸 **Español** → [`README-es.md`](README-es.md)

## İndirme

En güncel sürümü her zaman
**[en son sürüm sayfasından](https://github.com/Oynthe/dersis-app/releases/latest)**
edinebilirsiniz.

| İşletim sistemi | İndirilecek dosya |
|-----------------|-------------------|
| **Windows** | `Dersis_Setup_v<sürüm>.exe` |
| **macOS / Linux** | Hazır dosya yok — kaynaktan derlenir |

> **macOS ve Linux notu:** yayımlanan sürümlerde yalnızca Windows kurulum dosyası
> bulunur; bugüne kadar hiçbir sürüme Mac ya da Linux paketi eklenmemiştir. macOS
> derlemesi çalışır durumdadır ve bir Mac üzerinde kaynaktan üretilebilir:
> [`docs/MACOS.md`](docs/MACOS.md) ve dilinizin README dosyasındaki “Kaynak Koddan
> Çalıştırma” bölümüne bakın.

Komut satırını tercih edenler için, depodaki bağımlılık gerektirmeyen indirici en son sürümü
çözümler, indirir ve SHA-256 sağlamasını doğrular:

```bash
python scripts/download_release.py
```

## Lisansa genel bakış

DERSİS **tüm bireysel kullanıcılar için ücretsizdir**. **Kurumların** (üniversiteler,
fakülteler, okullar, bölümler, araştırma merkezleri, idari birimler veya herhangi bir
üniversite alt birimi) DERSİS'i kurumsal sistemlerine gömmek, entegre etmek, dağıtmak,
özelleştirmek veya resmî olarak dahil etmek için **ücretli bir lisansa ihtiyacı vardır**.
Kurumsal lisanslama için **[dersis.app@gmail.com](mailto:dersis.app@gmail.com)** ile iletişime
geçin. Tüm koşullar: [`LICENSE.md`](LICENSE.md).

## Hata bildirimi

Uygulama içindeki **Hata Bildir** düğmesini kullanın (sizin için bir e-posta hazırlar; uygulama
kendiliğinden hiçbir şey göndermez) veya doğrudan
**[dersis.app@gmail.com](mailto:dersis.app@gmail.com)** adresine e-posta gönderin.

## Geliştiriciler için

- Derleme ve paketleme kılavuzu: [`BUILD.md`](BUILD.md)
- Depo yapısı: [`docs/STRUCTURE.md`](docs/STRUCTURE.md)
- Özellik envanteri (kaynak konumlarıyla): [`docs/FEATURES.md`](docs/FEATURES.md)
