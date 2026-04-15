module.exports = {
  apps: [
    {
      name: 'mleads-dashboard',
      script: '/workspace/dashboard/server.py',
      interpreter: 'python3',
      args: '--db /workspace/.kortix/kortix.db --port 43123',
      env: {
        DASHBOARD_PORT: '43123',
        KORTIX_DB_PATH: '/workspace/.kortix/kortix.db',
      },
    },
    {
      name: 'mleads-stripe-webhook',
      script: '/workspace/scripts/stripe_webhook.py',
      interpreter: 'python3',
      args: '--db /workspace/.kortix/kortix.db --port 43124',
      env: {
        STRIPE_WEBHOOK_PORT: '43124',
        KORTIX_DB_PATH: '/workspace/.kortix/kortix.db',
      },
    },
    {
      name: 'mleads-autowork',
      script: '/workspace/scripts/run_autowork_cycle.sh',
      interpreter: 'bash',
      cron_restart: '*/15 * * * *',
      autorestart: false,
      env: {
        KORTIX_DB_PATH: '/workspace/.kortix/kortix.db',
        KORTIX_SHARED_CONTEXT_PATH: '/workspace/.kortix/memory/shared-context.json',
        AUTOWORK_BATCH_SIZE: '50',
        AUTOWORK_MAX_CONCURRENT: '5',
      },
    },
  ],
}
