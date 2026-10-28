// The share bundle has no public dir, so the brand robot icon (public/icons/robot.svg) is inlined as a data URI.
import robotSvg from '../../public/icons/robot.svg?raw'
import { registerBrandRobotIcon } from '../utils/brandRobotIconLibrary'

registerBrandRobotIcon(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(robotSvg)}`)
