# Cold Open: finding the 40 seconds that make a stranger stay

## Why this problem

TODO(Kartik)

## Results on the full pack

14 episodes; 62 raw candidates; 38 valid.

| # | Cut | Hook | Last line | Engine | Why |
|---|---|---|---|---|---|
| 1 | Flash-forward, E11 00:19.40–00:22.06; E10 00:09.00–00:49.74 | Search her pockets. She is a thief! | Order! I will have order in my hall! | Family betrayal | [mock] Opens on a charged line at a tension peak and cuts before the reply. |
| 2 | Straight, E9 00:19.00–01:07.74 | Every wedding needs a widow. Why not two? | Every wedding needs a widow. Why not two? | Forbidden scandal | [mock] Opens on a charged line at a tension peak and cuts before the reply. |
| 3 | Straight, E3 00:27.00–01:17.60 | Search her pockets. She is a thief! | Lord Ashvale? That is impossible. | Framed, condemned | [mock] Opens on a charged line at a tension peak and cuts before the reply. |
| 4 | Flash-forward, E2 00:00.40–00:03.44; E1 00:10.00–00:51.00 | Every wedding needs a widow. Why not two? | Lord Ashvale? That is impossible. | Humiliation reversal | [mock] Opens on a charged line at a tension peak and cuts before the reply. |
| 5 | Flash-forward, E8 00:17.40–00:20.44; E7 00:10.00–00:48.74 | Your father paid me to watch over you. | The ring smells of your rose oil, Selene. | Other | [mock] Opens on a charged line at a tension peak and cuts before the reply. |

## How I know it's good

- TODO: hit rate vs gold (run coldopen eval)
- TODO: precision@10 (coldopen eval --rate)
- TODO: median cut error vs gold
- 100%: of final promos pass every hard rule

## Where it fails

- Stranger confused (E3 00:52.00–01:27.74): Cold Viewer comprehension 0.38. [mock] keyword overlap only
- Stranger confused (E5 00:06.40–00:09.44; E4 00:24.00–00:58.00): Cold Viewer comprehension 0.38. [mock] keyword overlap only
- Stranger confused (E5 00:22.00–01:06.00): Cold Viewer comprehension 0.38. [mock] keyword overlap only

## Scale and cost

152 calls, 84,093 tokens, cost unknown (prices.yaml not filled), 0 s.

## Tools used and where they got it wrong

TODO(Kartik). Suggested: Mock provider guessed list semantics; Rate limiter tied to one event loop
