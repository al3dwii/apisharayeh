package o2.policy
default allow := false
allow if {
  input.tenant == "tenantA"
  input.tool_id == "echo"
}
