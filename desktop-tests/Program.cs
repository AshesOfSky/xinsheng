using System.Text.Json;
using Echo.Desktop;

var tracker = new IncomingMessageTracker();
var assertions = 0;
void Observe(bool expected, string contact, params (string Id, string Author, string Origin)[] rows)
{
    using var state = JsonDocument.Parse(JsonSerializer.Serialize(new
    {
        config = new { accountId = "10001", contactId = contact },
        messages = rows.Select(row => new { id = row.Id, author = row.Author, origin = row.Origin })
    }));
    var actual = tracker.Observe(state.RootElement);
    if (actual != expected) throw new Exception($"Case {assertions + 1}: expected {expected}, got {actual}");
    assertions++;
}

Observe(false, "20002", ("old-live", "contact", "live")); // Opening with existing messages.
Observe(false, "20002", ("old-live", "contact", "live"), ("history", "contact", "history"));
Observe(false, "20002", ("old-live", "contact", "live"), ("human", "human", "live"));
Observe(false, "20002", ("old-live", "contact", "live"), ("own-echo", "assistant", "live"));
Observe(true, "20002", ("old-live", "contact", "live"), ("new-1", "contact", "live"));
Observe(false, "20002", ("new-1", "contact", "live")); // Repeated polling does not retrigger.
Observe(false, "20002"); // A page without live incoming messages does not reset the marker.
Observe(false, "20002", ("new-1", "contact", "live"));
Observe(true, "20002", ("new-2", "contact", "live"));
Observe(false, "30003", ("existing-other-contact", "contact", "live")); // Switching contact establishes a new baseline.
Observe(true, "30003", ("new-other-contact", "contact", "live"));
Observe(false, "40004");
Observe(true, "40004", ("first-ever", "contact", "live"));
Console.WriteLine($"Passed {assertions} incoming-message notification checks.");
