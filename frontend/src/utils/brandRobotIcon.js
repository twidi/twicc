/**
 * Swap Font Awesome's `robot` icon for our own (`public/icons/robot.svg`,
 * the TwiCC logo robot, upright) everywhere a `<wa-icon name="robot">` is
 * rendered — the default icon library is wrapped, every other icon still
 * resolves to Font Awesome. Unlike a regular icon it is NOT monochrome: the
 * head is always the flat brand cyan (no gradient or shadow, unlike the logo)
 * and the eyes and mouth always white, whatever the surrounding colour. A
 * working agent's robot is animated (styles/robot-working.css).
 */
import { resolvePublicAssetUrl } from './publicAsset'
import { registerBrandRobotIcon } from './brandRobotIconLibrary'

registerBrandRobotIcon(resolvePublicAssetUrl('icons/robot.svg'))
