pkgname=predator-sense
pkgver=r46.31a106a
pkgrel=1
pkgdesc="Predator Sense fan control app for Helios 300 G3-572-55UB"
arch=('x86_64')
url="https://github.com/iashutoshtiwari/predator-sense"
license=('MIT')
depends=('python' 'python-pyqt6' 'polkit')
makedepends=('git')
optdepends=('evtest: monitor keyboard events while troubleshooting')
source=("${pkgname}::git+${url}.git")
sha256sums=('SKIP')

pkgver() {
  cd "${srcdir}/${pkgname}"
  printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short HEAD)"
}

package() {
  cd "${srcdir}/${pkgname}"

  install -dm755 "${pkgdir}/usr/share/predator-sense"
  install -m644 main.py "${pkgdir}/usr/share/predator-sense/main.py"
  install -m644 frontend.py "${pkgdir}/usr/share/predator-sense/frontend.py"
  install -m644 ecwrite.py "${pkgdir}/usr/share/predator-sense/ecwrite.py"
  install -m644 font_config.py "${pkgdir}/usr/share/predator-sense/font_config.py"
  install -m644 app_icon.ico "${pkgdir}/usr/share/predator-sense/app_icon.ico"

  install -dm755 "${pkgdir}/usr/share/predator-sense/fonts"
  install -m644 fonts/* "${pkgdir}/usr/share/predator-sense/fonts/"

  install -dm755 "${pkgdir}/usr/share/fonts/TTSquares"
  install -m644 fonts/* "${pkgdir}/usr/share/fonts/TTSquares/"

  install -dm755 "${pkgdir}/usr/bin"
  install -m755 packaging/predator-sense "${pkgdir}/usr/bin/predator-sense"

  install -dm755 "${pkgdir}/usr/share/applications"
  install -m644 packaging/predator-sense.desktop "${pkgdir}/usr/share/applications/predator-sense.desktop"

  install -dm755 "${pkgdir}/usr/share/polkit-1/actions"
  install -m644 packaging/org.predatorsense.policy "${pkgdir}/usr/share/polkit-1/actions/org.predatorsense.policy"
}
