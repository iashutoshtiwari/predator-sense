pkgname=predator-sense
pkgver=0.2.0
pkgrel=1
pkgdesc="Predator Sense fan control app for Helios 300 G3-572-55UB"
arch=('x86_64')
url="local"
license=('MIT')
depends=('python' 'python-pyqt6' 'polkit')
install="${pkgname}.install"
optdepends=('evtest: monitor keyboard events while troubleshooting')
source=()
sha256sums=()

pkgver() {
  # For local builds we just echo the static pkgver
  printf "%s" "${pkgver}"
}

package() {
  cd "${startdir}"

  install -dm755 "${pkgdir}/usr/share/predator-sense"
  install -dm755 "${pkgdir}/usr/share/predator-sense/src"
  install -dm755 "${pkgdir}/usr/share/predator-sense/src/core"
  install -dm755 "${pkgdir}/usr/share/predator-sense/src/ui"

  install -m644 src/main.py "${pkgdir}/usr/share/predator-sense/src/main.py"
  install -m644 src/frontend.py "${pkgdir}/usr/share/predator-sense/src/frontend.py"
  install -m644 src/font_config.py "${pkgdir}/usr/share/predator-sense/src/font_config.py"
  install -m644 src/core/__init__.py "${pkgdir}/usr/share/predator-sense/src/core/__init__.py"
  install -m644 src/core/logger.py "${pkgdir}/usr/share/predator-sense/src/core/logger.py"
  install -m644 src/core/env_checks.py "${pkgdir}/usr/share/predator-sense/src/core/env_checks.py"
  install -m644 src/core/hardware.py "${pkgdir}/usr/share/predator-sense/src/core/hardware.py"
  install -m644 src/core/profiles.py "${pkgdir}/usr/share/predator-sense/src/core/profiles.py"
  install -m644 src/ui/__init__.py "${pkgdir}/usr/share/predator-sense/src/ui/__init__.py"
  install -m644 src/ui/main_window.py "${pkgdir}/usr/share/predator-sense/src/ui/main_window.py"

  install -m644 background_service.py "${pkgdir}/usr/share/predator-sense/background_service.py"
  install -m644 app_icon.ico "${pkgdir}/usr/share/predator-sense/app_icon.ico"

  install -dm755 "${pkgdir}/usr/share/predator-sense/fonts"
  install -m644 fonts/* "${pkgdir}/usr/share/predator-sense/fonts/"

  install -dm755 "${pkgdir}/usr/share/fonts/TTSquares"
  install -m644 fonts/* "${pkgdir}/usr/share/fonts/TTSquares/"

  install -dm755 "${pkgdir}/usr/bin"
  install -m755 packaging/predator-sense "${pkgdir}/usr/bin/predator-sense"
  install -m755 packaging/predator-sense-root "${pkgdir}/usr/bin/predator-sense-root"

  install -dm755 "${pkgdir}/usr/share/applications"
  install -m644 packaging/predator-sense.desktop "${pkgdir}/usr/share/applications/predator-sense.desktop"

  install -dm755 "${pkgdir}/usr/share/polkit-1/actions"
  install -m644 packaging/org.predatorsense.policy "${pkgdir}/usr/share/polkit-1/actions/org.predatorsense.policy"

  install -dm755 "${pkgdir}/usr/lib/systemd/system"
  install -m644 packaging/predator-sense.service "${pkgdir}/usr/lib/systemd/system/predator-sense.service"
}
