using System;
using System.Collections.Generic;

namespace SalahAIBridge
{
    /// <summary>
    /// The last high-level request the user queued from the ribbon — a Create Web
    /// App prompt or a Publish request. The MCP server fetches it via the
    /// <c>get_request</c> bridge command so Claude / Antigravity can act on it.
    /// In-process only; not persisted. This is how a button "bridges to" the agent:
    /// MCP is client→server, so the add-in cannot call the agent; instead it parks
    /// the request here and the agent picks it up on its next poll.
    /// </summary>
    internal static class AppRequest
    {
        public static string Kind { get; private set; }       // "web_app" | "publish"
        public static string Text { get; private set; }       // the user's prompt / details
        public static DateTime? CreatedAt { get; private set; }

        public static void Set(string kind, string text)
        {
            Kind = kind;
            Text = text;
            CreatedAt = DateTime.Now;
        }

        public static void Clear()
        {
            Kind = null;
            Text = null;
            CreatedAt = null;
        }

        public static Dictionary<string, object> Snapshot() => new()
        {
            ["pending"] = CreatedAt != null,
            ["kind"] = Kind,
            ["text"] = Text,
            ["created_at"] = CreatedAt?.ToString("o"),
        };
    }
}
