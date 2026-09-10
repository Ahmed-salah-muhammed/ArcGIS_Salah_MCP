using System.Text.Json;
using System.Threading.Tasks;

namespace SalahAIBridge.Commands
{
    /// <summary>
    /// <c>get_request</c> — params <c>{ clear? }</c> → <c>{ pending, kind, text,
    /// created_at }</c>. Returns the last request the user queued from the ribbon
    /// (Create Web App prompt / Publish) so the agent can pick it up and execute it.
    /// Pass <c>{"clear": true}</c> to consume it. No project access -> no QueuedTask.
    /// </summary>
    public sealed class GetRequestCommand : ICommand
    {
        public Task<object> ExecuteAsync(JsonElement @params)
        {
            object data = AppRequest.Snapshot();
            if (ParamUtil.GetBool(@params, "clear"))
                AppRequest.Clear();
            return Task.FromResult(data);
        }
    }
}
