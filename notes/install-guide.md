# Installing the chat widget

For customers, or for whoever sets up their website. Fill in the two values in the snippet before sending.

## What you need from us

- **Your site key**, which looks like `pk_...`. It is not a secret: it appears in your page's HTML and only names your account. Security comes from the domain list below.
- **Confirmation that your domains are on the allowed list.** Send every domain the chat will run on, for example `yourschool.com`. Subdomains such as `www.yourschool.com` are covered automatically. A staging or preview site on a different domain must be added separately.

## The snippet

Paste this just before the closing `</body>` tag on every page where the chat should appear:

```html
<script src="https://sitechat-api-uukevi3qpq-uc.a.run.app/widget/widget.js"
        data-site-key="pk_YOUR_KEY"></script>
```

Do not add `async` or `type="module"` to the tag. The widget reads its own script tag to find the site key.

The chat button shows a few of your suggested questions in turn, to invite a click. To keep it still instead, add `data-teaser="off"` to the tag.

## WordPress

1. Install the free plugin **WPCode** (Plugins → Add New → search "WPCode").
2. Go to Code Snippets → Header & Footer.
3. Paste the snippet into **Footer**, then Save.

Some themes have their own "footer scripts" box under Appearance → Customize; that works too. Clear any caching plugin afterwards.

## Wix

Custom code needs a premium plan with a connected domain.

1. Settings → Custom Code (under Advanced) → Add Custom Code.
2. Paste the snippet, choose **All pages**, and place it in **Body - end**.
3. Apply, then publish the site.

## Squarespace

Code injection needs a Business plan or higher.

1. Settings → Advanced → Code Injection.
2. Paste the snippet into **Footer**, then Save.

## Shopify

1. Online Store → Themes → … → Edit code.
2. Open `layout/theme.liquid` and paste the snippet just before `</body>`.
3. Save.

## Plain HTML sites

Paste the snippet before `</body>` in each page, or in the shared footer template.

## Checking it works

Open the site and look for the round chat button in the bottom-right corner. Ask a question.

If the chat says **"This chat is not available right now"**, open the browser console (right-click → Inspect → Console) and look for a line starting with `[sitechat]`:

| Message | What to do |
|---|---|
| The script tag needs a data-site-key attribute | The `data-site-key` part of the snippet is missing. |
| The site key in the script tag is not recognised | Check the key was copied exactly. |
| This site key is not allowed on *yourdomain*, or the chat is switched off | Send us that domain to add to your allowed list. |

## What to give the assistant

The assistant answers from the documents and the facts sheet you give us. Treat everything in them as public: someone who tries hard enough can get the assistant to repeat any of it. Include only what you would put on your website.

Leave out: staff-only phone numbers or emails, discount or booking codes, passwords or login details, internal or wholesale prices, supplier details, and notes meant for your team.

In the facts sheet, state facts ("Course fee: USD 1,450"). Do not write instructions to the assistant ("always say spaces are limited"): such rows are skipped, and we will let you know which ones.

## Privacy

The widget shows "AI assistant · Answers can be wrong" under the message box. If you send us the address of your privacy policy, it is linked there too. Visitors who give their email are told it goes to your team and that they are copied on the email; your privacy policy should say the same.
