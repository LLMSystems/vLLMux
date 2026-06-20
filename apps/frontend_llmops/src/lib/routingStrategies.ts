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

export const ROUTING_STRATEGY_LABELS: Record<string, string> = {
  least_load: '最低負載（預設）',
  round_robin: '輪詢',
  random: '隨機',
  least_inflight: '最少進行中',
  p2c: '二選一取優',
  session_affinity: '會話黏性',
  prefix_affinity: '前綴黏性',
}

export const routingStrategyLabel = (s: string) => ROUTING_STRATEGY_LABELS[s] ?? s
