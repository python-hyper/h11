Preserve the waiting-for-100-continue state after informational responses
such as 103 Early Hints, so servers still send 100 Continue before waiting
for the request body.
