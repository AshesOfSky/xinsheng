using System.Text.Json;

namespace Echo.Desktop;

// The first snapshot establishes a baseline: reopening the window is not a new message.
internal sealed class IncomingMessageTracker
{
    private string? conversation;
    private string? lastIncomingId;

    public bool Observe(JsonElement state)
    {
        var config = state.GetProperty("config");
        var current = config.GetProperty("accountId").GetString() + ":" + config.GetProperty("contactId").GetString();
        string? latest = null;
        foreach (var message in state.GetProperty("messages").EnumerateArray())
        {
            if (message.GetProperty("author").GetString() == "contact" && message.GetProperty("origin").GetString() == "live")
                latest = message.GetProperty("id").GetString();
        }
        if (conversation != current)
        {
            conversation = current; lastIncomingId = latest; return false;
        }
        if (latest is null || latest == lastIncomingId) return false;
        lastIncomingId = latest;
        return true;
    }
}
