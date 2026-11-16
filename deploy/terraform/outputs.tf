output "app_namespace" {
  value = module.platform.app_namespace
}

output "app_release" {
  description = "rOracle helm release status (null until the app layer is enabled)."
  value       = one(module.app[*].status)
}

output "url" {
  description = "Public URL (ingress host)."
  value       = var.ingress.host == "" ? null : "https://${var.ingress.host}"
}

output "placeholders_remaining" {
  description = "Placeholder values still set on enabled components (apply fails unless allow_placeholders=true)."
  value       = local.placeholders
}

output "inference_endpoints" {
  description = "In-cluster inference endpoints (empty when the module is disabled)."
  value       = local.inference_enabled ? module.inference[0].endpoints : {}
}
