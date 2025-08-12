{{- define "agentic.name" -}}
agentic
{{- end -}}

{{- define "agentic.fullname" -}}
{{ include "agentic.name" . }}
{{- end -}}
