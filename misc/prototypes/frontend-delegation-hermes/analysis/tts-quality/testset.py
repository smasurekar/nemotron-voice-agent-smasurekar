"""TTS quality test set: 41 sentences in 5 categories, written the way the speaker would say it in a tau3 call.

speaker: "agent" = the voice agent (Magpie's real job, LLM-written text with symbols),
         "caller" = the simulated user (gpt-4o-mini-tts's real job, gpt-5.2-written text, things spelled out).
entities: key strings that must survive TTS -> ASR, in *normalised* form (see textnorm.py); matched as a
contiguous token sequence in the normalised hypothesis.
"""

CATEGORIES = {
    "plain": "Plain conversational",
    "numbers": "Numbers, prices, dates, times",
    "names": "Spelled-out names and letters",
    "emails": "Emails",
    "ids": "IDs and digits",
}

SENTENCES = [
    # 1. plain conversational
    ("p01", "plain", "agent", "Sure, I can help you with that. Could you tell me a bit more about what went wrong with your order?", []),
    ("p02", "plain", "agent", "I'm sorry to hear about the delay. Let me look into your reservation and see what options we have.", []),
    ("p03", "plain", "caller", "Hi, yeah, I need to cancel my flight. Something came up at work and I can't travel next week.", []),
    ("p04", "plain", "caller", "No, I'd rather keep the original payment method. Can you just refund it to the credit card instead?", []),
    ("p05", "plain", "agent", "Your internet should be working again. Please restart the router and let me know if the light turns green.", []),
    ("p06", "plain", "caller", "Okay, I turned airplane mode off and restarted the phone, but I still don't have any signal.", []),
    ("p07", "plain", "agent", "Before I make this change, please confirm that you want to exchange the blue jacket for the black one.", []),
    ("p08", "plain", "caller", "Yes, that's correct. Go ahead and process the exchange, and send the new one to my home address.", []),
    # 2. numbers, prices, dates, times
    ("n01", "numbers", "agent", "The total for your new flight is $1,286.40, which will be charged to your credit card.", ["1286 dollars 40 cents"]),
    ("n02", "numbers", "agent", "Your flight departs on May 15th at 5:30 PM from Newark.", ["may 15", "530 pm"]),
    ("n03", "numbers", "caller", "I was charged fifty four dollars and twenty cents, but the item was only thirty nine dollars and ninety nine cents.", ["54 dollars 20 cents", "39 dollars 99 cents"]),
    ("n04", "numbers", "caller", "My data plan is two point zero gigabytes, and I've already used one point eight this month.", ["2 point 0 gigabytes", "1 point 8"]),
    ("n05", "numbers", "agent", "The refund of $54.20 will appear on your statement in 5 to 7 business days.", ["54 dollars 20 cents", "7 business days"]),
    ("n06", "numbers", "caller", "The flight was on March 3rd, and it was delayed by about three and a half hours.", ["march 3"]),
    ("n07", "numbers", "agent", "Your new departure is Tuesday, June 4th, at 11:45 AM, arriving at 2:10 PM.", ["june 4", "1145 am", "210 pm"]),
    ("n08", "numbers", "caller", "I'd like to upgrade two passengers, and my budget is around five hundred dollars total.", ["500 dollars"]),
    # 3. spelled-out names and letters
    ("s01", "names", "caller", "My last name is Rossi, R O S S I.", ["rossi"]),
    ("s02", "names", "caller", "It's Chen, C as in Charlie, H, E, N.", ["chen"]),
    ("s03", "names", "caller", "My first name is Daiki, D A I K I, and my last name is Sanchez.", ["daiki", "sanchez"]),
    ("s04", "names", "caller", "The reservation is under Sofia Li. That's Sofia with an F, and Li is L I.", ["sofia", "li"]),
    ("s05", "names", "caller", "My last name is Kovacs, K O V A C S.", ["kovacs"]),
    ("s06", "names", "agent", "Thank you, Mr. Kowalski. I've found the booking for you and Ms. Nguyen.", ["kowalski", "nguyen"]),
    ("s07", "names", "caller", "The name is Yusuf Ahmed, Y U S U F.", ["yusuf", "ahmed"]),
    ("s08", "names", "agent", "Just to confirm, that's Daiki Sanchez, spelled D A I K I?", ["daiki", "sanchez"]),
    # 4. emails
    ("e01", "emails", "caller", "Sure, my email is mia dot garcia four seven at example dot com.", ["mia dot garcia 47"]),
    ("e02", "emails", "agent", "I have your email as daiki.sanchez1479@example.com. Is that right?", ["daiki dot sanchez 1479"]),
    ("e03", "emails", "caller", "It's sofia dot li nine two one nine at example dot com.", ["sofia dot li 9219"]),
    ("e04", "emails", "agent", "I'll send the receipt to omar.anderson3203@example.com.", ["omar dot anderson 3203"]),
    ("e05", "emails", "caller", "My email address is yusuf dot rossi seven seven six nine at example dot com.", ["yusuf dot rossi 7769"]),
    ("e06", "emails", "agent", "The confirmation went to ethan.kim8811@example.com a few minutes ago.", ["ethan dot kim 8811"]),
    ("e07", "emails", "caller", "Uh, it's raj, R A J, dot kovacs, at example dot com.", ["raj dot kovacs"]),
    ("e08", "emails", "agent", "Could you confirm the email on file? I see lucas.brown9081@example.com.", ["lucas dot brown 9081"]),
    # 5. ids and digits
    ("d01", "ids", "agent", "I found order #W4073673. It was delivered on Monday.", ["w 4073673"]),
    ("d02", "ids", "caller", "The order number is W, four three one six, one five two.", ["w 4316152"]),
    ("d03", "ids", "caller", "My user id is mei underscore kovacs underscore eight zero two zero.", ["mei underscore kovacs underscore 8020"]),
    ("d04", "ids", "agent", "Your user ID is mei_kovacs_8020, and your zip code is 19122.", ["mei underscore kovacs underscore 8020", "19122"]),
    ("d05", "ids", "caller", "You can reach me at five five five, one two three, two zero zero two.", ["5551232002"]),
    ("d06", "ids", "agent", "The phone number on the account is 555-123-2002.", ["5551232002"]),
    ("d07", "ids", "caller", "My zip code is one nine one two two.", ["19122"]),
    ("d08", "ids", "agent", "Your reservation ID is 4WQ150, on flight HAT214.", ["4 wq 150", "hat 214"]),
    ("d09", "ids", "caller", "My reservation number is four, W, Q, one, five, zero.", ["4 wq 150"]),
]


def rows() -> list[dict]:
    return [{"id": i, "category": c, "speaker": s, "text": t, "entities": e} for i, c, s, t, e in SENTENCES]
