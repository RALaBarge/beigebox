# Stripe Setup Guide (15 Minutes)

## Step 1: Create Stripe Account (5 min)

1. Go to https://stripe.com
2. Click "Start now"
3. Fill in:
   - Email: your email
   - Password: secure password
   - Accept terms
4. You're in the dashboard

## Step 2: Add Business Info (3 min)

1. Go to Settings → Business Profile
2. Fill in:
   - **Business name:** "BeigeBox" (or "BeigeBox AI")
   - **Website:** https://aisolutionsunlimited.com/beigebox
   - **Business type:** "Software as a Service (SaaS)"
   - **Description:** "LLM security control plane"
3. Save

## Step 3: Create Products (5 min)

In Stripe dashboard, go to Products → Add product

### Product 1: BeigeBox Indie
- **Name:** BeigeBox Indie
- **Description:** 1 LLM instance, cloud-hosted, audit logging, basic detection
- **Price:** $99/month (set recurring)
- Create

### Product 2: BeigeBox Team
- **Name:** BeigeBox Team
- **Description:** 5 instances, advanced detection, compliance reports, Slack alerts
- **Price:** $499/month (set recurring)
- Create

### Product 3: BeigeBox Enterprise
- **Name:** BeigeBox Enterprise
- **Description:** Unlimited instances, DLP, extraction detection, SOC 2 features, priority support
- **Price:** $999/month (set recurring, mark as custom if you prefer "Contact Sales")
- Create

## Step 4: Generate Checkout Links (2 min)

For each product:
1. Go to Products → [Product Name]
2. Scroll to "Pricing" section
3. Click on the price
4. Copy the "Payment Link" URL
5. You now have checkout URLs for each plan

Example links will look like:
```
https://checkout.stripe.com/pay/cs_live_a1b2c3d4e5f6...
```

## Step 5: Wire Up Your Landing Page

In the HTML landing page we created:
1. Replace `onclick="alert('Stripe checkout link will be here')"` with `onclick="window.location='[PAYMENT_LINK]'"`
2. Or use:
```html
<a href="[PAYMENT_LINK]" class="cta-button">Get Started</a>
```

Examples:
```html
<!-- Indie Button -->
<a href="https://checkout.stripe.com/pay/cs_live_indie_link" class="cta-button">Get Started</a>

<!-- Team Button -->
<a href="https://checkout.stripe.com/pay/cs_live_team_link" class="cta-button">Get Started</a>

<!-- Enterprise Contact Button -->
<a href="mailto:hello@aisolutionsunlimited.com?subject=BeigeBox%20Enterprise%20Inquiry" class="cta-button">Contact Sales</a>
```

## Step 6: Test (2 min)

1. Click your payment button
2. Use Stripe test card: `4242 4242 4242 4242`
3. Any future date, any CVC
4. Verify it works

## Step 7: Add Bank Account (Optional, Can Do Later)

In Stripe → Settings → Bank accounts

Fill in routing number + account number from your bank. Stripe will verify with 2 small deposits ($0.01-$0.05) within 1-2 days.

**You don't NEED this before launch** — customers won't actually charge until you add it. But do it within a week so actual payments process.

---

## Quick Reference: Stripe Dashboard

- **Products:** https://dashboard.stripe.com/products
- **Payments:** https://dashboard.stripe.com/payments
- **Settings:** https://dashboard.stripe.com/settings
- **Test Mode:** Toggle "View Test Data" in top right (test cards work in test mode)

---

## That's It

You now have:
- ✅ 3 products in Stripe
- ✅ 3 payment links
- ✅ Billing system ready
- ✅ Everything wired up to your landing page

When someone clicks "Get Started", they go straight to Stripe checkout.

When they enter card info + confirm, Stripe handles the transaction and (once you add bank account) deposits money to your account within 1-2 business days.

---

## If You Get Stuck

- Stripe support: https://support.stripe.com
- Docs: https://stripe.com/docs
- They're very good at help

You've got this. 15 minutes and you're done.
