import { computed } from 'vue'

import { usePeersStore } from '../stores/peers'
import { useSettingsStore } from '../stores/settings'

/**
 * Whether the peer system is configured — the single condition that decides if
 * the peer surfaces (sidebar inbox button, Settings › Peers actions) exist at
 * all.
 *
 * The address is what makes the feature usable — `PeersManagerDialog` gates its
 * own "Add a peer" form on the same getter — but peers and messages outlive it:
 * an address can be removed while a relationship and its history remain, and
 * those stay worth reaching. Nothing at all means the Peers section is still a
 * setup form, and every entry point would lead nowhere.
 *
 * Orthogonal to `peersStore.inboxCount`: pending work drives the badge, not the
 * surfaces themselves — an empty inbox stays reachable.
 */
export function usePeerSystemConfigured() {
    const settingsStore = useSettingsStore()
    const peersStore = usePeersStore()

    return computed(() =>
        !!settingsStore.getUsablePeerBaseUrl
        || peersStore.peers.length > 0
        || peersStore.messages.length > 0,
    )
}
