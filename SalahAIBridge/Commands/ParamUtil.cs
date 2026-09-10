using System.Collections.Generic;
using System.Text.Json;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// Small helpers for pulling typed values out of a command's <c>params</c>
    /// JSON element. All are null/missing-safe: a missing key (or a <c>params</c>
    /// that isn't an object) yields the supplied fallback / an empty collection.
    /// </summary>
    internal static class ParamUtil
    {
        public static string GetString(JsonElement p, string name, string fallback = null)
        {
            if (p.ValueKind == JsonValueKind.Object &&
                p.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String)
                return v.GetString();
            return fallback;
        }

        public static bool GetBool(JsonElement p, string name, bool fallback = false)
        {
            if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty(name, out var v))
            {
                if (v.ValueKind == JsonValueKind.True) return true;
                if (v.ValueKind == JsonValueKind.False) return false;
            }
            return fallback;
        }

        public static int GetInt(JsonElement p, string name, int fallback)
        {
            if (p.ValueKind == JsonValueKind.Object &&
                p.TryGetProperty(name, out var v) &&
                v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var i))
                return i;
            return fallback;
        }

        public static List<string> GetStringList(JsonElement p, string name)
        {
            var list = new List<string>();
            if (p.ValueKind == JsonValueKind.Object &&
                p.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Array)
                foreach (var e in v.EnumerateArray())
                    if (e.ValueKind == JsonValueKind.String)
                        list.Add(e.GetString());
            return list;
        }

        /// <summary>Positional GP args as CLR objects (string/long/double/bool/null).</summary>
        public static object[] GetObjectArray(JsonElement p, string name)
        {
            var list = new List<object>();
            if (p.ValueKind == JsonValueKind.Object &&
                p.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Array)
                foreach (var e in v.EnumerateArray())
                    list.Add(JsonToClr(e));
            return list.ToArray();
        }

        /// <summary>An object of string→string pairs (e.g. GP environment overrides).</summary>
        public static IEnumerable<KeyValuePair<string, string>> GetStringMap(JsonElement p, string name)
        {
            var list = new List<KeyValuePair<string, string>>();
            if (p.ValueKind == JsonValueKind.Object &&
                p.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Object)
                foreach (var prop in v.EnumerateObject())
                    list.Add(new KeyValuePair<string, string>(
                        prop.Name,
                        prop.Value.ValueKind == JsonValueKind.String ? prop.Value.GetString() : prop.Value.ToString()));
            return list;
        }

        private static object JsonToClr(JsonElement e) => e.ValueKind switch
        {
            JsonValueKind.String => e.GetString(),
            JsonValueKind.Number => e.TryGetInt64(out var l) ? l : e.GetDouble(),
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            JsonValueKind.Null => null,
            _ => e.ToString(),
        };
    }
}
