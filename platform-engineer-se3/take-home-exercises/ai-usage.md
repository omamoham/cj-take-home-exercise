# AI Usage Disclosure

## 1. Which tool you used
Claude, Google Gemini


## 2. Prompt summaries or prompts
I asked Claude to help me recreate part 2 and part 3 on my local kind cluster where I had a cluster setup for my tester online shopping app.

1. I asked it to help me create a custom cjpod kubernetes resource operator.
2. Asked for potential imrovements for the inhibit_rules in alertmanager config.


## 3. Suggestions you accepted
I accepted python code for the custom cjpod resource operator. It was something, I could verify to be correct as my previous experience was working with (KOPF) kubernetes Operator Pythonic Framework.
I accepted Test code for the operator testing.
i accepted code to fill up test persisten volume claim to debug the broken routing of alertmanager.


## 4. Suggestions you rejected
It initially gave me a operator code that was in go programming language that was unnecessarily complex. I rejected it and guided it through prompts to provide me with simpler python code, since I kopf was something I had prior knowledge of.

It gave me broken persistent volume claim filler code. I corrected it and ensured the alerts were being fired.


## 5. What you independently verified
Operator code debugging through official kopf documentation.
Prometheus documentation to verify by examples
