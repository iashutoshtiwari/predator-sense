# Local release build: python scripts/prepare_arch_source.py, then makepkg -si.
# Before AUR publication replace the local archive with an immutable release URL.
pkgname=predator-sense
pkgver=1.0.0
pkgrel=1
pkgdesc="Native fan control and telemetry for Acer Predator G3-572"
arch=('x86_64')
url="https://github.com/iashutoshtiwari/predator-sense"
license=('GPL-3.0-only')
depends=('python' 'python-pyqt6' 'python-dbus-next' 'polkit' 'dbus' 'systemd' 'kmod'
         'qt6-wayland' 'qt6-svg' 'hicolor-icon-theme')
makedepends=('python-build' 'python-installer' 'python-setuptools')
optdepends=('python-nvidia-ml-py: NVIDIA GPU temperature via NVML')
backup=('etc/modprobe.d/predator-sense.conf')
install=predator-sense.install
source=("$pkgname-$pkgver.tar.gz")
sha256sums=('aaf2312d959b80f2619097b83c6165e76d0db4167691cfc004fd816493a23910')

build() {
  cd "$srcdir/$pkgname-$pkgver"
  python -m build --wheel --no-isolation
}

package() {
  cd "$srcdir/$pkgname-$pkgver"
  python -m installer --destdir="$pkgdir" --prefix=/usr dist/*.whl

  install -Dm644 packaging/predator-sensed.service "$pkgdir/usr/lib/systemd/system/predator-sensed.service"
  install -Dm644 packaging/predator-sense-modprobe.conf "$pkgdir/etc/modprobe.d/predator-sense.conf"
  install -Dm644 packaging/io.github.iashutoshtiwari.PredatorSense.service \
    "$pkgdir/usr/share/dbus-1/system-services/io.github.iashutoshtiwari.PredatorSense.service"
  install -Dm644 packaging/io.github.iashutoshtiwari.PredatorSense.conf \
    "$pkgdir/usr/share/dbus-1/system.d/io.github.iashutoshtiwari.PredatorSense.conf"
  install -Dm644 packaging/io.github.iashutoshtiwari.predatorsense.policy \
    "$pkgdir/usr/share/polkit-1/actions/io.github.iashutoshtiwari.predatorsense.policy"
  install -Dm644 packaging/predator-sense.desktop \
    "$pkgdir/usr/share/applications/io.github.iashutoshtiwari.PredatorSense.desktop"
  install -Dm644 src/predator_sense/assets/predator-sense.svg \
    "$pkgdir/usr/share/icons/hicolor/scalable/apps/io.github.iashutoshtiwari.PredatorSense.svg"
  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
}
