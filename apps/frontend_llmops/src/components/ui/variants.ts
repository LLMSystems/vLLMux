import { cva, type VariantProps } from 'class-variance-authority'

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50 cursor-pointer [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground shadow-sm hover:bg-primary/90',
        destructive:
          'bg-destructive text-white shadow-sm hover:bg-destructive/90 focus-visible:ring-destructive',
        outline:
          'border border-input bg-background/40 shadow-sm hover:bg-accent hover:text-accent-foreground',
        secondary: 'bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        success:
          'bg-status-ready/15 text-status-ready border border-status-ready/30 hover:bg-status-ready/25',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-9 px-4 py-2',
        sm: 'h-8 rounded-md px-3 text-xs',
        lg: 'h-10 rounded-md px-6',
        icon: 'size-9',
        'icon-sm': 'size-8',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)
export type ButtonVariants = VariantProps<typeof buttonVariants>

export const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium transition-colors whitespace-nowrap',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary/10 text-primary',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'text-foreground border-border',
        muted: 'border-transparent bg-muted text-muted-foreground',
        ready: 'border-status-ready/30 bg-status-ready/12 text-status-ready',
        starting: 'border-status-starting/30 bg-status-starting/12 text-status-starting',
        stopping: 'border-status-stopping/30 bg-status-stopping/12 text-status-stopping',
        failed: 'border-status-failed/30 bg-status-failed/12 text-status-failed',
        stopped: 'border-status-stopped/30 bg-status-stopped/12 text-status-stopped',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)
export type BadgeVariants = VariantProps<typeof badgeVariants>
