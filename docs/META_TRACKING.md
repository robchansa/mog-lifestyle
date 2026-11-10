# Preorder signup measurement

Dataset: **MOG Lifestyle | Website signups**, ID **1439220394903801**.

The public homepage loads a local consent controller. It loads Meta's Pixel
only after a separate **Allow ad measurement** choice. Existing Google
Analytics consent does not authorize Meta. Global Privacy Control and Do Not
Track disable Meta, including when an earlier opt-in was saved. The footer's
**Ad measurement choices** link allows withdrawal and clears Meta cookies.
The controller does not run on account, checkout, contact, search or admin
pages. It also refuses unexpected query parameters and private referrers.

After consent it sends `PageView`. A `Lead` event is sent only when the live
Notify Me endpoint saves a new subscription or renews an unsubscribed one.
The event contains the fixed content name "Preorder notification signup" and
a random event ID. No email, name, form value or customer ID is passed to
Meta. Automatic configuration and advanced matching are not enabled. Failed,
spam-trapped, duplicate active and demo submissions do not produce a Lead.
Submissions without consent, with blocked tracking, or without JavaScript are
not reported to Meta; the store still saves valid signups normally.

## Verification

1. Events Manager: select this dataset and open **Test events**.
2. Visit the public homepage and choose **Allow ad measurement**. The optional
   Google Analytics choice is separate and can be declined.
3. Verify a browser `PageView`, then submit a new test address controlled by
   the tester. Verify exactly one `Lead` after the success confirmation.
4. Verify that declining ad measurement still allows a signup but sends no
   Meta event, and that resubmitting an active address does not add a Lead.
5. Leave **automatic advanced matching** off in dataset Settings. Assign only
   the intended ad account if it is not already connected.
6. For signup optimization use a Website Leads ad set and select this dataset
   and `Lead` once received. A profile-visit campaign does not optimize for
   website signups. Review the destination, budget and schedule before
   publishing; deploying this integration does not publish any ads.

The Python suite tests server validation and deduplication. On systems with
Node, it also runs `tests/meta_tracking.cjs`, which behaviorally tests consent,
revocation, privacy signals, safe page scope and event deduplication without
making network requests.
