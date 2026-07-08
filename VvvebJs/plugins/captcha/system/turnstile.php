<?php

namespace Vvveb\Plugins\Captcha\System;

class Turnstile {
	function validate($secret, $response = null) {
		$remote_addr = $_SERVER['REMOTE_ADDR'];
		$url         = 'https://challenges.cloudflare.com/turnstile/v0/siteverify';
		$token       = $response ?? $_POST['cf-turnstile-response'] ?? false;
		// Request data
		$data = [
			'secret'   => $secret,
			'response' => $token,
			'remoteip' => $remote_addr,
		];
		// Initialize cURL
		$curl = curl_init();
		// Set the cURL options
		curl_setopt($curl, CURLOPT_URL, $url);
		curl_setopt($curl, CURLOPT_POST, true);
		curl_setopt($curl, CURLOPT_POSTFIELDS, $data);
		curl_setopt($curl, CURLOPT_RETURNTRANSFER, true);
		// Execute the cURL request
		$response = curl_exec($curl);
		// Check for errors
		$success = true;

		if (curl_errno($curl)) {
			$success = curl_error($curl);
		} else {
			$data    = json_decode($response, true);
			$success = $data['success'] ?? false;
		}
		// Close cURL
		if (PHP_MAJOR_VERSION < 8) {
			curl_close($curl);
		}

		return $success;
	}
}
?>    
