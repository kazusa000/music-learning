-- Read-only Kontakt source key-map metadata query.
-- Fill each @@...@@ string with a Lua-quoted value before execution.
-- The caller must already have loaded exactly one source instrument into an
-- isolated empty rack. This query never loads, saves, edits, or plays it.
-- See docs/architecture/kontakt-source-keymap.md for API provenance/schema.

local OUTPUT_PATH = "@@OUTPUT_PATH@@"
local SOURCE_NKI_SHA256 = "@@SOURCE_NKI_SHA256@@"
local SOURCE_COMPONENT_SHA256 = "@@SOURCE_COMPONENT_SHA256@@"
local RESTORED_STATE_SHA256 = "@@RESTORED_STATE_SHA256@@"
local PLUGIN_SHA256 = "@@PLUGIN_SHA256@@"
local VST3_CLASS_ID = "@@VST3_CLASS_ID@@"
local RESTORE_ID = "@@RESTORE_ID@@"
local PHASE = "@@PHASE@@"

local MAX_INSTRUMENTS = 256
local MAX_INSTRUMENT_INDEX = 65535
local MAX_GROUPS = 65536
local MAX_ZONES = 100000
local MAX_SCRIPT_SLOTS = 64
local MAX_SCRIPT_BYTES = 1048576
local MAX_OUTPUT_BYTES = 33554432
local MAX_VALUE_DEPTH = 16
local MAX_VALUE_NODES = 200000

local function require_filled(value, label)
  if type(value) ~= "string" or value == "" or string.find(value, "@@", 1, true) then
    error("unfilled_probe_tag:" .. label)
  end
  return value
end

local function require_sha(value, label)
  require_filled(value, label)
  if #value ~= 64 or string.find(value, "[^0-9a-f]") then error("invalid_sha256:" .. label) end
end

require_filled(OUTPUT_PATH, "output_path")
require_sha(SOURCE_NKI_SHA256, "source_nki_sha256")
require_sha(SOURCE_COMPONENT_SHA256, "source_component_sha256")
require_sha(RESTORED_STATE_SHA256, "restored_state_sha256")
require_sha(PLUGIN_SHA256, "plugin_sha256")
require_filled(VST3_CLASS_ID, "vst3_class_id")
if #VST3_CLASS_ID ~= 32 or string.find(VST3_CLASS_ID, "[^0-9a-fA-F]") then error("invalid_vst3_class_id") end
require_filled(RESTORE_ID, "restore_id")
if string.find(RESTORE_ID, "[^A-Za-z0-9_-]") then error("invalid_restore_id") end
if PHASE ~= "first-load" and PHASE ~= "reopen" then error("invalid_phase") end

local function hex(value)
  local text = tostring(value or "")
  local parts = {}
  for i = 1, #text do parts[i] = string.format("%02x", string.byte(text, i)) end
  return table.concat(parts)
end

local function serialize(value, depth, seen, state)
  state.nodes = state.nodes + 1
  if state.nodes > MAX_VALUE_NODES then error("metadata_value_node_limit") end
  if value == nil then return "z" end
  local kind = type(value)
  if kind == "string" then return "s:" .. hex(value) end
  if kind == "boolean" then return value and "b:1" or "b:0" end
  if kind == "number" then
    if value ~= value or value == math.huge or value == -math.huge then error("non_finite_metadata_number") end
    return "n:" .. string.format("%.17g", value)
  end
  if kind ~= "table" then error("unsupported_metadata_value_type:" .. kind) end
  if depth >= MAX_VALUE_DEPTH then error("metadata_value_depth_limit") end
  if seen[value] then error("cyclic_metadata_table") end
  seen[value] = true
  local keys = {}
  for key in pairs(value) do
    local key_type = type(key)
    if key_type ~= "string" and key_type ~= "number" then error("unsupported_metadata_table_key") end
    keys[#keys + 1] = key
  end
  table.sort(keys, function(left, right)
    local left_type, right_type = type(left), type(right)
    if left_type ~= right_type then return left_type < right_type end
    return tostring(left) < tostring(right)
  end)
  local parts = {}
  for _, key in ipairs(keys) do
    parts[#parts + 1] = serialize(key, depth + 1, seen, state) .. "=" ..
      serialize(value[key], depth + 1, seen, state)
  end
  seen[value] = nil
  return "t{" .. table.concat(parts, ",") .. "}"
end

local function encoded(value)
  local ok, result = pcall(serialize, value, 0, {}, {nodes = 0})
  if not ok then return "unknown", hex(tostring(result)) end
  return "known", result
end

local function api_call(name, ...)
  local member_ok, method = pcall(function() return Kontakt[name] end)
  if not member_ok then return {status = "unknown", detail = tostring(method)} end
  if type(method) ~= "function" then return {status = "unknown", detail = "api_member_unavailable"} end
  local call_ok, value = pcall(method, ...)
  if not call_ok then return {status = "unknown", detail = tostring(value)} end
  return {status = "known", value = value}
end

local function api_property(name)
  local ok, value = pcall(function() return Kontakt[name] end)
  if not ok then return {status = "unknown", detail = tostring(value)} end
  if value == nil then return {status = "unknown", detail = "api_property_unavailable"} end
  return {status = "known", value = value}
end

local function checked(result, predicate, label)
  if result.status ~= "known" then return result end
  local ok, valid = pcall(predicate, result.value)
  if not ok or not valid then return {status = "unknown", detail = "invalid_result:" .. label} end
  return result
end

local function emit(file, state, ...)
  local fields = {...}
  local line = table.concat(fields, "\t") .. "\n"
  if state.bytes + #line > MAX_OUTPUT_BYTES then error("metadata_output_limit") end
  local ok, write_error = file:write(line)
  if not ok then error("metadata_write_failed:" .. tostring(write_error)) end
  state.bytes = state.bytes + #line
end

local function emit_result(file, state, record, field, result)
  local status, value
  if result.status == "known" then
    status, value = encoded(result.value)
  else
    status, value = "unknown", hex(result.detail or "api_result_unknown")
  end
  if status == "unknown" then state.unknown = state.unknown + 1 end
  emit(file, state, record, field, status, value)
  return status, value
end

local function emit_option(file, state, field, options_result)
  if options_result.status ~= "known" or type(options_result.value) ~= "table" then
    state.unknown = state.unknown + 1
    emit(file, state, "option", field, "unknown", hex(options_result.detail or "instrument_options_unavailable"))
    return
  end
  local value = options_result.value[field]
  local status, payload
  if value == nil then
    status, payload = "omitted", "z"
  else
    status, payload = encoded(value)
    if status == "unknown" then state.unknown = state.unknown + 1 end
  end
  emit(file, state, "option", field, status, payload)
end

local function emit_script(file, state, instrument_index, script_index)
  local name = checked(api_call("get_instrument_script_name", instrument_index, script_index), function(value)
    return type(value) == "string"
  end, "script_name")
  local linked = checked(api_call("is_instrument_script_linked", instrument_index, script_index), function(value)
    return type(value) == "boolean"
  end, "script_linked")
  local linked_file = checked(api_call("get_instrument_script_linked_filename", instrument_index, script_index), function(value)
    return value == nil or type(value) == "string"
  end, "script_linked_filename")
  local bypassed = checked(api_call("is_instrument_script_bypassed", instrument_index, script_index), function(value)
    return type(value) == "boolean"
  end, "script_bypassed")
  local protected = checked(api_call("is_instrument_script_protected", instrument_index, script_index), function(value)
    return type(value) == "boolean"
  end, "script_protected")
  local name_status, name_value = emit_result(file, state, "script", "name_" .. script_index, name)
  local linked_status, linked_value = emit_result(file, state, "script", "linked_" .. script_index, linked)
  local linked_file_status, linked_file_value = emit_result(file, state, "script", "linked_file_" .. script_index, linked_file)
  local bypassed_status, bypassed_value = emit_result(file, state, "script", "bypassed_" .. script_index, bypassed)
  local protected_status, protected_value = emit_result(file, state, "script", "protected_" .. script_index, protected)

  if protected.status == "known" and protected.value == true then
    state.unknown = state.unknown + 1
    emit(file, state, "script_source", tostring(script_index), "protected", "")
  else
    local source = api_call("get_instrument_script_source", instrument_index, script_index)
    if source.status ~= "known" then
      state.unknown = state.unknown + 1
      emit(file, state, "script_source", tostring(script_index), "unknown", hex(source.detail or "api_result_unknown"))
    elseif type(source.value) ~= "string" then
      state.unknown = state.unknown + 1
      emit(file, state, "script_source", tostring(script_index), "unknown", hex("invalid_script_source_type"))
    elseif #source.value > MAX_SCRIPT_BYTES then
      state.unknown = state.unknown + 1
      emit(file, state, "script_source", tostring(script_index), "over_limit", tostring(#source.value))
    elseif source.value == "" then
      emit(file, state, "script_source", tostring(script_index), "empty", "")
    else
      emit(file, state, "script_source", tostring(script_index), "known", hex(source.value))
    end
  end
  -- Keep the emitted results above even for empty script slots: a failed getter
  -- is distinct from a known nil/empty return.
  return name_status, name_value, linked_status, linked_value, linked_file_status, linked_file_value,
    bypassed_status, bypassed_value, protected_status, protected_value
end

local function query_one_instrument(file, state, instrument_index)
  local name = checked(api_call("get_instrument_name", instrument_index), function(value)
    return type(value) == "string"
  end, "instrument_name")
  emit_result(file, state, "instrument", "name", name)

  local channel = checked(api_call("get_instrument_midi_channel", instrument_index), function(value)
    return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= 64
  end, "instrument_midi_channel")
  emit_result(file, state, "instrument", "midi_channel", channel)

  local options = checked(api_call("get_instrument_options", instrument_index), function(value)
    return type(value) == "table"
  end, "instrument_options")
  emit_result(file, state, "instrument", "options", options)
  for _, field in ipairs({"key_switch", "key_range_from", "key_range_to", "velocity_range_from", "velocity_range_to"}) do
    emit_option(file, state, field, options)
  end

  local groups = checked(api_call("get_num_groups", instrument_index), function(value)
    return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= MAX_GROUPS
  end, "group_count")
  local group_count_status = emit_result(file, state, "instrument", "group_count", groups)
  if group_count_status == "known" then
    for group_index = 0, groups.value - 1 do
      emit(file, state, "group_index", tostring(group_index))
      emit_result(file, state, "group", "name_" .. group_index,
        checked(api_call("get_group_name", instrument_index, group_index), function(value)
          return type(value) == "string"
        end, "group_name"))
      emit_result(file, state, "group", "start_options_" .. group_index,
        checked(api_call("get_group_start_options", instrument_index, group_index), function(value)
          return type(value) == "table"
        end, "group_start_options"))
    end
  end

  local zones = checked(api_call("get_num_zones", instrument_index), function(value)
    return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= MAX_ZONES
  end, "zone_count")
  emit_result(file, state, "instrument", "zone_count", zones)
  if zones.status == "known" then
    for zone_index = 0, zones.value - 1 do
      emit(file, state, "zone_index", tostring(zone_index))
      local zone_fields = {
        {"group", "get_zone_group", function(value)
          return type(value) == "number" and value % 1 == 0 and value >= 0 and value < MAX_GROUPS
        end},
        {"low_key", "get_zone_low_key", function(value)
          return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= 127
        end},
        {"high_key", "get_zone_high_key", function(value)
          return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= 127
        end},
        {"low_velocity", "get_zone_low_velocity", function(value)
          return type(value) == "number" and value == value and value >= 0 and value <= 127
        end},
        {"high_velocity", "get_zone_high_velocity", function(value)
          return type(value) == "number" and value == value and value >= 0 and value <= 127
        end},
        {"root_key", "get_zone_root_key", function(value)
          return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= 127
        end},
        {"geometry", "get_zone_geometry", function(value) return type(value) == "table" end},
      }
      for _, field in ipairs(zone_fields) do
        emit_result(file, state, "zone_" .. zone_index, field[1],
          checked(api_call(field[2], instrument_index, zone_index), field[3], field[1]))
      end
      local sample = api_call("get_zone_sample", instrument_index, zone_index)
      if sample.status ~= "known" then
        state.unknown = state.unknown + 1
        emit(file, state, "zone_" .. zone_index, "sample_presence", "unknown", hex(sample.detail or "api_result_unknown"))
      elseif sample.value == nil or sample.value == "" then
        emit(file, state, "zone_" .. zone_index, "sample_presence", "absent", "")
      elseif type(sample.value) == "string" then
        emit(file, state, "zone_" .. zone_index, "sample_presence", "present", "")
      else
        state.unknown = state.unknown + 1
        emit(file, state, "zone_" .. zone_index, "sample_presence", "unknown", hex("invalid_sample_result_type"))
      end
    end
  end

  local script_capacity = checked(api_property("max_num_scripts"), function(value)
    return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= MAX_SCRIPT_SLOTS
  end, "max_num_scripts")
  emit_result(file, state, "instrument", "script_capacity", script_capacity)
  if script_capacity.status == "known" then
    for script_index = 0, script_capacity.value - 1 do
      emit_script(file, state, instrument_index, script_index)
    end
  end
end

local function run_capture()
  local existing = io.open(OUTPUT_PATH, "rb")
  if existing then existing:close(); error("refusing_existing_output") end
  local temporary_path = OUTPUT_PATH .. ".tmp"
  local temporary_existing = io.open(temporary_path, "rb")
  if temporary_existing then temporary_existing:close(); error("refusing_existing_temporary_output") end
  local file, open_error = io.open(temporary_path, "wb")
  if not file then error("metadata_output_open_failed:" .. tostring(open_error)) end
  local state = {bytes = 0, unknown = 0}
  local ok, failure = pcall(function()
    emit(file, state, "schema", "kontakt-source-keymap-v1")
    emit(file, state, "identity", "source_nki_sha256", hex(SOURCE_NKI_SHA256))
    emit(file, state, "identity", "source_component_sha256", hex(SOURCE_COMPONENT_SHA256))
    emit(file, state, "identity", "restored_state_sha256", hex(RESTORED_STATE_SHA256))
    emit(file, state, "identity", "plugin_sha256", hex(PLUGIN_SHA256))
    emit(file, state, "identity", "vst3_class_id", hex(VST3_CLASS_ID))
    emit(file, state, "identity", "restore_id", hex(RESTORE_ID))
    emit(file, state, "identity", "phase", hex(PHASE))
    emit(file, state, "runtime", "kontakt_version", "unknown", hex("no documented version getter in cited read API"))

    local count = checked(api_call("get_num_instruments"), function(value)
      return type(value) == "number" and value % 1 == 0 and value >= 0 and value <= MAX_INSTRUMENTS
    end, "instrument_count")
    emit_result(file, state, "context", "instrument_count", count)
    local indices = checked(api_call("get_instrument_indices"), function(value)
      return type(value) == "table" and #value <= MAX_INSTRUMENTS
    end, "instrument_indices")
    emit_result(file, state, "context", "instrument_indices", indices)

    if count.status == "known" and indices.status == "known" then
      if count.value == 1 and #indices.value == 1 then
        local index = indices.value[1]
        if type(index) == "number" and index % 1 == 0 and index >= 0 and index <= MAX_INSTRUMENT_INDEX then
          emit(file, state, "context", "selected_instrument_index", "known", tostring(index))
          query_one_instrument(file, state, index)
        else
          state.unknown = state.unknown + 1
          emit(file, state, "context", "selected_instrument_index", "unknown", hex("invalid_index"))
        end
      else
        state.unknown = state.unknown + 1
        emit(file, state, "context", "selected_instrument_index", "unknown", hex("requires_exactly_one_instrument"))
      end
    else
      state.unknown = state.unknown + 1
      emit(file, state, "context", "selected_instrument_index", "unknown", hex("instrument_inventory_unavailable"))
    end
    emit(file, state, "summary", state.unknown == 0 and "metadata_complete" or "metadata_partial",
      "getter_unknown_count", tostring(state.unknown))
    local flush_ok, flush_error = file:flush()
    if not flush_ok then error("metadata_flush_failed:" .. tostring(flush_error)) end
  end)
  file:close()
  if not ok then error(tostring(failure)) end
  local renamed, rename_error = os.rename(temporary_path, OUTPUT_PATH)
  if not renamed then error("metadata_publish_failed:" .. tostring(rename_error)) end
end

run_capture()
