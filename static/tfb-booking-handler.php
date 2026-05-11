<?php
/**
 * Plugin Name: TFB Booking Form Handler
 * Description: Handles booking form submissions from the standalone index.html page.
 *              Sends a booking notification to info@theflyingbike.com.au and an
 *              acknowledgement email to the customer. Uses wp_mail() so all mail
 *              routes through WP Mail SMTP / SendLayer automatically.
 * Version:     1.2.0
 * Author:      The Flying Bike
 */

if ( ! defined( 'ABSPATH' ) ) {
    exit;
}

/* ══════════════════════════════════════════════════════════════════════════
 * CONFIGURATION
 *
 * TFB_BOOKING_SECRET — a private token the form sends with every submission.
 * It prevents random bots from posting to the endpoint directly.
 *
 * You can override this in wp-config.php instead of editing this file:
 *   define( 'TFB_BOOKING_SECRET', 'your-own-secret-string-here' );
 *
 * The same value must be set in index.html — search for TFB_SECRET.
 * ══════════════════════════════════════════════════════════════════════════ */
if ( ! defined( 'TFB_BOOKING_SECRET' ) ) {
    define( 'TFB_BOOKING_SECRET', 'tfb-flyingbike-2026' );
}

/* ── CORS headers ─────────────────────────────────────────────────────────── */
add_action( 'init', function () {
    $origin = isset( $_SERVER['HTTP_ORIGIN'] ) ? rtrim( $_SERVER['HTTP_ORIGIN'], '/' ) : '';
    $allowed = [
        'https://theflyingbike.com.au',
        'http://theflyingbike.com.au',
        'https://www.theflyingbike.com.au',
    ];
    header( 'Access-Control-Allow-Origin: ' . ( in_array( $origin, $allowed, true ) ? $origin : $allowed[0] ) );
    header( 'Access-Control-Allow-Methods: POST, OPTIONS' );
    header( 'Access-Control-Allow-Headers: Content-Type' );
    if ( isset( $_SERVER['REQUEST_METHOD'] ) && $_SERVER['REQUEST_METHOD'] === 'OPTIONS' ) {
        http_response_code( 204 );
        exit;
    }
} );

/* ── AJAX endpoints ───────────────────────────────────────────────────────── */
add_action( 'wp_ajax_nopriv_tfb_booking', 'tfb_handle_booking' );
add_action( 'wp_ajax_tfb_booking',        'tfb_handle_booking' );

function tfb_handle_booking() {

    /* 1. Parse JSON body */
    $raw  = file_get_contents( 'php://input' );
    $data = json_decode( $raw, true );

    if ( ! is_array( $data ) || empty( $data ) ) {
        tfb_log( 'Invalid body. Raw: ' . substr( $raw ?? '', 0, 300 ) );
        wp_send_json_error( [ 'message' => 'Invalid request.' ], 400 );
    }

    /* 2. Verify secret token */
    $submitted = isset( $data['_secret'] ) ? $data['_secret'] : '';
    if ( ! hash_equals( TFB_BOOKING_SECRET, $submitted ) ) {
        tfb_log( 'Secret mismatch. Got: ' . $submitted );
        wp_send_json_error( [ 'message' => 'Unauthorised.' ], 403 );
    }

    /* 3. Sanitise */
    $name     = sanitize_text_field(     $data['name']     ?? '' );
    $email    = sanitize_email(          $data['email']    ?? '' );
    $phone    = sanitize_text_field(     $data['phone']    ?? '' );
    $suburb   = sanitize_text_field(     $data['suburb']   ?? '' );
    $services = sanitize_text_field(     $data['services'] ?? '' );
    $message  = sanitize_textarea_field( $data['message']  ?? '' );

    /* 4. Validate */
    if ( empty( $name ) || empty( $email ) || empty( $suburb ) ) {
        wp_send_json_error( [ 'message' => 'Please fill in all required fields.' ], 422 );
    }
    if ( ! is_email( $email ) ) {
        wp_send_json_error( [ 'message' => 'Please enter a valid email address.' ], 422 );
    }

    /* 5. Business notification — plain text */
    $sent = wp_mail(
        'info@theflyingbike.com.au',
        "{$name} - {$suburb}",
        tfb_booking_notification_text( $name, $email, $phone, $suburb, $services, $message ),
        [ 'Content-Type: text/plain; charset=UTF-8', "Reply-To: {$name} <{$email}>" ]
    );

    if ( ! $sent ) {
        global $phpmailer;
        $err = isset( $phpmailer ) ? $phpmailer->ErrorInfo : 'unknown';
        tfb_log( "wp_mail FAILED (business). Error: {$err}" );
        wp_send_json_error( [ 'message' => 'Mail delivery failed. Please call 0403 225 135 or email info@theflyingbike.com.au directly.' ], 500 );
    }
    tfb_log( "Business notification sent — {$name} <{$email}>" );

    /* 6. Customer acknowledgement */
    $sent_ack = wp_mail(
        $email,
        'Thanks for your booking request – The Flying Bike',
        tfb_acknowledgement_html( $name, $services ),
        [ 'Content-Type: text/html; charset=UTF-8', 'From: The Flying Bike <info@theflyingbike.com.au>' ]
    );
    if ( ! $sent_ack ) {
        tfb_log( "Acknowledgement FAILED for: {$email}" );
    } else {
        tfb_log( "Acknowledgement sent — {$email}" );
    }

    wp_send_json_success( [ 'message' => 'Booking received.' ] );
}

/* ── Logging ──────────────────────────────────────────────────────────────── */
function tfb_log( $msg ) {
    if ( defined( 'WP_DEBUG_LOG' ) && WP_DEBUG_LOG ) {
        error_log( '[TFB Booking] ' . $msg );
    }
}

/* ══════════════════════════════════════════════════════════════════════════
 * EMAIL TEMPLATES
 * ══════════════════════════════════════════════════════════════════════════ */

function tfb_booking_notification_text( $name, $email, $phone, $suburb, $services, $message ) {
    $phone_display   = $phone   ? $phone   : 'Not provided';
    $message_display = $message ? $message : 'None';
    $date            = wp_date( 'l j F Y, g:i a' );

    return "New booking request received on {$date}

Name: {$name}
Email: {$email}
Phone: {$phone_display}
Suburb: {$suburb}

Service Type:
{$services}

Message:
{$message_display}
";
}

function tfb_acknowledgement_html( $name, $services ) {
    $parts  = explode( ' ', trim( $name ) );
    $first  = esc_html( $parts[0] );
    $svc    = esc_html( $services );
    $year   = wp_date( 'Y' );

    return "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'></head>
<body style='margin:0;padding:0;background:#f0f4f0;font-family:Helvetica Neue,Arial,sans-serif;'>
<table width='100%' cellpadding='0' cellspacing='0' style='background:#f0f4f0;padding:32px 0;'>
<tr><td align='center'><table width='600' cellpadding='0' cellspacing='0' style='max-width:600px;width:100%;'>
<tr><td style='background:#0f1710;border-radius:10px 10px 0 0;padding:32px 36px;text-align:center;'>
  <p style='margin:0;font-size:26px;font-weight:900;color:#f5f7f2;'>🚲 The Flying Bike</p>
  <p style='margin:8px 0 0;font-size:13px;color:#8fa88a;letter-spacing:.15em;text-transform:uppercase;'>Melbourne's Mobile Bicycle Workshop</p>
</td></tr>
<tr><td style='background:linear-gradient(90deg,#2d6a35,#3d8f47);height:4px;'></td></tr>
<tr><td style='background:#fff;padding:40px 36px;'>
  <p style='margin:0 0 20px;font-size:22px;font-weight:700;color:#0f1710;'>Thanks, {$first}! 👋</p>
  <p style='margin:0 0 16px;font-size:15px;color:#444;line-height:1.7;'>We've received your service booking request and will be in touch shortly to confirm a time that works for you.</p>
  <table width='100%' cellpadding='0' cellspacing='0' style='background:#f5f9f5;border:1px solid #d0e4d0;border-radius:8px;margin:24px 0;overflow:hidden;'>
    <tr><td style='padding:16px 20px;border-bottom:1px solid #d0e4d0;'><p style='margin:0;font-size:11px;font-weight:700;color:#2d6a35;letter-spacing:.15em;text-transform:uppercase;'>What You Requested</p></td></tr>
    <tr><td style='padding:16px 20px;'><p style='margin:0;font-size:14px;color:#1a1a1a;line-height:1.6;'>{$svc}</p></td></tr>
  </table>
  <p style='margin:0 0 16px;font-size:15px;color:#444;line-height:1.7;'>If you need to reach us in the meantime:</p>
  <table cellpadding='0' cellspacing='0' style='margin-bottom:10px;'>
    <tr><td style='font-size:18px;padding-right:12px;vertical-align:top;padding-top:2px;'>📞</td>
    <td><p style='margin:0;font-size:13px;color:#888;'>Phone</p><a href='tel:0403225135' style='font-size:15px;font-weight:700;color:#2d6a35;text-decoration:none;'>0403 225 135</a></td></tr>
  </table>
  <table cellpadding='0' cellspacing='0' style='margin-bottom:32px;'>
    <tr><td style='font-size:18px;padding-right:12px;vertical-align:top;padding-top:2px;'>✉️</td>
    <td><p style='margin:0;font-size:13px;color:#888;'>Email</p><a href='mailto:info@theflyingbike.com.au' style='font-size:15px;font-weight:700;color:#2d6a35;text-decoration:none;'>info@theflyingbike.com.au</a></td></tr>
  </table>
  <div style='text-align:center;'>
    <a href='https://theflyingbike.com.au' style='display:inline-block;background:#e8a020;color:#0f1710;padding:14px 36px;border-radius:6px;font-weight:700;font-size:14px;letter-spacing:.08em;text-transform:uppercase;text-decoration:none;'>Visit Our Website</a>
  </div>
</td></tr>
<tr><td style='background:#0f1710;border-radius:0 0 10px 10px;padding:24px 36px;'>
  <p style='margin:0 0 8px;text-align:center;font-size:13px;color:#8fa88a;'>Follow us for cycling tips and updates</p>
  <p style='margin:0;text-align:center;'>
    <a href='https://www.instagram.com/theflyingbike/' style='color:#e8a020;text-decoration:none;font-size:13px;margin:0 8px;'>Instagram</a>
    &nbsp;·&nbsp;
    <a href='https://www.facebook.com/The-Flying-Bike-393907894145951/' style='color:#e8a020;text-decoration:none;font-size:13px;margin:0 8px;'>Facebook</a>
  </p>
  <p style='margin:16px 0 0;text-align:center;font-size:11px;color:#4a5e4b;'>
    © {$year} The Flying Bike · Melbourne, Australia ·
    <a href='https://theflyingbike.com.au/contact-us/' style='color:#4a5e4b;'>Terms &amp; Conditions</a>
  </p>
</td></tr>
</table></td></tr></table>
</body></html>";
}
