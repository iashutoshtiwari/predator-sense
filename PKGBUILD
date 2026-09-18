pkgname=predator-sense
pkgver=0.2.0
pkgrel=5
pkgdesc="Predator Sense fan control app for Helios 300 G3-572-55UB"
arch=('x86_64')
url="local"
license=('MIT')
depends=('python' 'python-pyqt6' 'python-dbus-next' 'polkit' 'dbus' 'qt6-wayland' 'qt6-svg')
install="${pkgname}.install"
optdepends=('evtest: monitor keyboard events while troubleshooting'
            'python-nvidia-ml-py: NVIDIA GPU temperature via NVML')
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
  install -dm755 "${pkgdir}/usr/share/predator-sense/src/service"

  install -m644 src/main.py "${pkgdir}/usr/share/predator-sense/src/main.py"
  install -m644 src/frontend.py "${pkgdir}/usr/share/predator-sense/src/frontend.py"
  install -m644 src/font_config.py "${pkgdir}/usr/share/predator-sense/src/font_config.py"
  install -m644 src/core/__init__.py "${pkgdir}/usr/share/predator-sense/src/core/__init__.py"
  install -m644 src/core/logger.py "${pkgdir}/usr/share/predator-sense/src/core/logger.py"
  install -m644 src/core/env_checks.py "${pkgdir}/usr/share/predator-sense/src/core/env_checks.py"
  install -m644 src/core/hardware.py "${pkgdir}/usr/share/predator-sense/src/core/hardware.py"
  install -m644 src/core/errors.py "${pkgdir}/usr/share/predator-sense/src/core/errors.py"
  install -m644 src/core/state.py "${pkgdir}/usr/share/predator-sense/src/core/state.py"
  install -m644 src/core/profiles.py "${pkgdir}/usr/share/predator-sense/src/core/profiles.py"
  install -m644 src/ui/__init__.py "${pkgdir}/usr/share/predator-sense/src/ui/__init__.py"
  install -m644 src/ui/main_window.py "${pkgdir}/usr/share/predator-sense/src/ui/main_window.py"
  install -m644 src/ui/theme.py "${pkgdir}/usr/share/predator-sense/src/ui/theme.py"
  install -m644 src/ui/instruments.py "${pkgdir}/usr/share/predator-sense/src/ui/instruments.py"

  install -m644 src/daemon_main.py "${pkgdir}/usr/share/predator-sense/src/daemon_main.py"
  install -m644 src/service/__init__.py "${pkgdir}/usr/share/predator-sense/src/service/__init__.py"
  install -m644 src/service/protocol.py "${pkgdir}/usr/share/predator-sense/src/service/protocol.py"
  install -m644 src/service/controller.py "${pkgdir}/usr/share/predator-sense/src/service/controller.py"
  install -m644 src/service/lifecycle.py "${pkgdir}/usr/share/predator-sense/src/service/lifecycle.py"
  install -m644 src/service/daemon.py "${pkgdir}/usr/share/predator-sense/src/service/daemon.py"
  install -m644 src/service/client.py "${pkgdir}/usr/share/predator-sense/src/service/client.py"
  install -m644 src/service/telemetry_model.py "${pkgdir}/usr/share/predator-sense/src/service/telemetry_model.py"
  install -m644 src/service/sensors.py "${pkgdir}/usr/share/predator-sense/src/service/sensors.py"
  install -m644 src/service/telemetry.py "${pkgdir}/usr/share/predator-sense/src/service/telemetry.py"
  install -Dm644 assets/predator-sense.svg "${pkgdir}/usr/share/predator-sense/assets/predator-sense.svg"
  install -Dm644 assets/predator-sense.svg "${pkgdir}/usr/share/icons/hicolor/scalable/apps/io.github.iashutoshtiwari.PredatorSense.svg"

  install -dm755 "${pkgdir}/usr/bin"
  install -m755 packaging/predator-sense "${pkgdir}/usr/bin/predator-sense"
  install -m755 packaging/predator-sensed "${pkgdir}/usr/bin/predator-sensed"

  install -dm755 "${pkgdir}/usr/share/applications"
  install -m644 packaging/predator-sense.desktop "${pkgdir}/usr/share/applications/io.github.iashutoshtiwari.PredatorSense.desktop"

  install -dm755 "${pkgdir}/usr/share/polkit-1/actions"
  install -m644 packaging/io.github.iashutoshtiwari.predatorsense.policy "${pkgdir}/usr/share/polkit-1/actions/io.github.iashutoshtiwari.predatorsense.policy"

  install -dm755 "${pkgdir}/usr/lib/systemd/system"
  install -m644 packaging/predator-sensed.service "${pkgdir}/usr/lib/systemd/system/predator-sensed.service"
  install -Dm644 packaging/io.github.iashutoshtiwari.PredatorSense.conf "${pkgdir}/usr/share/dbus-1/system.d/io.github.iashutoshtiwari.PredatorSense.conf"
  install -Dm644 packaging/io.github.iashutoshtiwari.PredatorSense.service "${pkgdir}/usr/share/dbus-1/system-services/io.github.iashutoshtiwari.PredatorSense.service"
}
