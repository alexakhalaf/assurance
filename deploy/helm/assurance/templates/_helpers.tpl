{{- define "assurance.name" -}}{{ .Chart.Name }}{{- end }}

{{- define "assurance.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- end }}

{{- define "assurance.labels" -}}
app.kubernetes.io/name: {{ include "assurance.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "assurance.selector" -}}
app.kubernetes.io/name: {{ include "assurance.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "assurance.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end }}

{{- define "assurance.secretName" -}}
{{ .Values.existingSecret | default (include "assurance.fullname" .) }}
{{- end }}

{{/* Environment shared by every container. Secrets come from the Secret, never from values in plain env. */}}
{{- define "assurance.env" -}}
- name: ASSURANCE_JOB_BACKEND
  value: rq
- name: ASSURANCE_LOG_JSON
  value: "true"
- name: ASSURANCE_MASTER_KEY_FILE
  value: /run/secrets/assurance/master-key
{{- range $k, $v := .Values.config }}
- name: ASSURANCE_{{ $k }}
  value: {{ $v | toString | quote }}
{{- end }}
{{- $s := include "assurance.secretName" . }}
{{- range $env, $key := dict "DATABASE_URL" "database-url" "REDIS_URL" "redis-url" "S3_ACCESS_KEY" "s3-access-key" "S3_SECRET_KEY" "s3-secret-key" }}
- name: ASSURANCE_{{ $env }}
  valueFrom: { secretKeyRef: { name: {{ $s }}, key: {{ $key }} } }
{{- end }}
{{- range $env, $key := dict "BOOTSTRAP_ADMIN_PASSWORD" "bootstrap-admin-password" "WEBHOOK_SECRET" "webhook-secret" "OIDC_CLIENT_SECRET" "oidc-client-secret" "SMTP_PASSWORD" "smtp-password" }}
- name: ASSURANCE_{{ $env }}
  valueFrom: { secretKeyRef: { name: {{ $s }}, key: {{ $key }}, optional: true } }
{{- end }}
{{- end }}

{{- define "assurance.volumes" -}}
- name: master-key
  secret:
    secretName: {{ include "assurance.secretName" . }}
    defaultMode: 0400
    items: [{ key: master-key, path: master-key }]
- name: tmp
  emptyDir: { sizeLimit: 2Gi }
- name: home
  emptyDir: { sizeLimit: 64Mi }
{{- end }}

{{- define "assurance.mounts" -}}
- { name: master-key, mountPath: /run/secrets/assurance, readOnly: true }
- { name: tmp, mountPath: /tmp }
- { name: home, mountPath: /home/assurance }
{{- end }}

{{/* Pod-level settings shared by every workload. */}}
{{- define "assurance.podCommon" -}}
{{- with .root.Values.imagePullSecrets }}
imagePullSecrets: {{ toYaml . | nindent 2 }}
{{- end }}
automountServiceAccountToken: false
securityContext: {{ toYaml .root.Values.podSecurityContext | nindent 2 }}
{{- with .root.Values.nodeSelector }}
nodeSelector: {{ toYaml . | nindent 2 }}
{{- end }}
{{- with .root.Values.tolerations }}
tolerations: {{ toYaml . | nindent 2 }}
{{- end }}
{{- if .root.Values.affinity }}
affinity: {{ toYaml .root.Values.affinity | nindent 2 }}
{{- end }}
{{- if .root.Values.topologySpread }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels: {{- include "assurance.selector" . | nindent 8 }}
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels: {{- include "assurance.selector" . | nindent 8 }}
{{- end }}
{{- end }}
