import i18n from '@/i18n'

/** Router load-balancing strategies — shared by the Traffic page selector and the
 *  model edit dialog. Keep in sync with router-server's STRATEGIES registry
 *  (apps/router-server/src/llm_router/routing_strategies.py).
 */
export const ROUTING_STRATEGIES = [
  'least_load',
  'round_robin',
  'random',
  'least_inflight',
  'p2c',
  'session_affinity',
  'prefix_affinity',
] as const

export type RoutingStrategy = (typeof ROUTING_STRATEGIES)[number]

export const routingStrategyLabel = (s: string) => {
  const key = `routingStrategies.${s}`
  const label = i18n.global.t(key)
  return label === key ? s : label
}
