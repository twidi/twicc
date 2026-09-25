/**
 * Swap Font Awesome's `robot` icon for our own (`public/icons/robot.svg`,
 * the TwiCC logo robot, upright) everywhere a `<wa-icon name="robot">` is
 * rendered — the default icon library is wrapped, every other icon still
 * resolves to Font Awesome. Unlike a regular icon it is NOT monochrome: the
 * head is always the brand blue and the eyes and mouth always white, whatever
 * the surrounding colour. A working agent's robot is animated (styles/robot-working.css).
 */
import { getIconLibrary, registerIconLibrary } from '@awesome.me/webawesome/dist/components/icon/library.js'
import { resolvePublicAssetUrl } from './publicAsset'

const ROBOT_ICON_URL = resolvePublicAssetUrl('icons/robot.svg')

const defaultLibrary = getIconLibrary('default')

if (defaultLibrary) {
    registerIconLibrary('default', {
        ...defaultLibrary,
        resolver: (name, family, variant, autoWidth) => (
            name === 'robot' ? ROBOT_ICON_URL : defaultLibrary.resolver(name, family, variant, autoWidth)
        ),
    })
}
