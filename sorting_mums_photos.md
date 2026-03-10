# Sorting through 12000 of my mum's photos
Oh my! There are a lot of things to do when someone dies. One that I found myself doing was going through my mum's photo collection. Not the old-school, curated photo albums of photos that have already been lovingly sorted, but the digital mass of photos that have come to be both a blessing and a curse.

Let me start by saying that my mum _loved_ taking photos. She was not, however, a very good photographer. This means that in amongst the 12k+ photos, there are a lot that are not even recognisable as _something_ let alone what she was trying to take photos of.

Being a (ahem, AI!) software engineer, I decided to throw technology at the problem. Let's go through the steps...

- [Sorting through 12000 of my mum's photos](#sorting-through-12000-of-my-mums-photos)
  - [Getting the photos](#getting-the-photos)
  - [Getting rid of duplicates](#getting-rid-of-duplicates)
  - [Getting rid of duplicates based on content (ignoring exif)](#getting-rid-of-duplicates-based-on-content-ignoring-exif)
  - [Reviewing photos](#reviewing-photos)
  - [Getting the best of a moment](#getting-the-best-of-a-moment)
  - [What to do with the rest?](#what-to-do-with-the-rest)
  - [What did I learn about photos that mean something?](#what-did-i-learn-about-photos-that-mean-something)
  - [What did I learn about my mum?](#what-did-i-learn-about-my-mum)
  - [What will I do differently?](#what-will-i-do-differently)

## Getting the photos

These photos were in my mum's icloud photos storage. I started by logging on as her (I had her iPad so could reset the password and answer 2FA authentication requests) and trying to download them. There is a limit of 1000 at a time - shouldn't be a problem, just do this 13 times and you've got the lot.

Except with the web interface that is a little tricky to do: simply selecting 1000 photos at a time is time consuming and after you've done that three times and then lose your place it feels like back to square 1. There must be a better way...

Enter scripting! There is already a github project to download all photos from a user's icloud photo album (put link in here) so I decided to use this. The documentation is _slightly_ lacking but with experimentation I managed to get the photos downloaded without too much trouble - even the 2FA was simple to use. Thank you <project>

## Getting rid of duplicates

12k photos is a lot to look through. Perhaps we can cut it down by getting rid of duplicates? This _should_ be easy, right, just traverse over the directory structure and take a hash of each file deleting one if you find a duplicate. Off to VS Code and copilot chat to create a script to do this for me (I didn't record the prompt but it basically worked first time). That's strange, only one duplicate pair in the entire 12k photos? Something fishy is going on here... metadata!

I don't know what it was that had changed the metadata, perhaps being arranged into albums in the Photos app or perhaps being transferred from a phone to a computer to a phone to a computer, but I guessed that the metadata had been changed. Next step, ignore metadata.

## Getting rid of duplicates based on content (ignoring exif)

## Reviewing photos

## Getting the best of a moment

## What to do with the rest?

## What did I learn about photos that mean something?

## What did I learn about my mum?

## What will I do differently?