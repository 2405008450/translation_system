export const GLOBAL_NOTIFICATIONS_REFRESH_EVENT = 'app:notifications-refresh'

export function refreshGlobalNotifications() {
  window.dispatchEvent(new CustomEvent(GLOBAL_NOTIFICATIONS_REFRESH_EVENT))
}
