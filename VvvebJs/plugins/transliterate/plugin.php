<?php

/**
 * Vvveb
 *
 * Copyright (C) 2022  Ziadin Givan
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

/*
Name: Transliterate slugs and urls
Slug: transliterate
Category: content
Url: https://plugins.vvveb.com/product/transliterate
Description: Transliterate post and products slugs and urls
Author: givanz
Version: 0.1
Thumb: transliterate.svg
Author url: https://www.vvveb.com
*/

use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

if (! function_exists('transliterate')) {
	function transliterate($text) {
		if (! $text) {
			return $text;
		}
		$table = [
			// global
			'Ґ'  => 'G', 'ґ'  => 'g', 'Є'  => 'Ie', 'є'  => 'ie', 'І'  => 'I', 'і'  => 'i', 'Ї'  => 'I', 'ї'  => 'i', 'Ї' => 'i', 'ї' => 'i', 'Ё' => 'Jo', 'ё' => 'jo', 'й' => 'i', 'Й' => 'I',
			//greek
			'α' => 'a', 'β' => 'v', 'γ' => 'g', 'δ' => 'd', 'ε' => 'e', 'ζ' => 'z', 'η' => 'i', 'ι' => 'i', 'κ' => 'k', 'λ' => 'l', 'μ' => 'm', 'ν' => 'n', 'ξ' => 'x', 'ο' => 'o', 'π' => 'p', 'ρ' => 'r', 'σ' => 's', 'τ' => 't', 'υ' => 'y', 'φ' => 'f', 'ω' => 'o', 'ά' => 'a', 'ί' => 'i', 'ύ' => 'y', 'έ' => 'e', 'ό' => 'o', 'ή' => 'i', 'ώ' => 'o', 'ϊ' => 'i', 'ϋ' => 'e', 'ΐ' => 'i', 'ΰ' => 'y', 'ς' => 's', 'Α' => 'A', 'Β' => 'V', 'Γ' => 'G', 'Δ' => 'D', 'Ε' => 'E', 'Ζ' => 'Z', 'Η' => 'I', 'Ι' => 'I', 'Κ' => 'K', 'Λ' => 'L', 'Μ' => 'M', 'Ν' => 'N', 'Ξ' => 'X', 'Ο' => 'O', 'Π' => 'P', 'Ρ' => 'R', 'Σ' => 'S', 'Τ' => 'T', 'Υ' => 'Y', 'Φ' => 'F', 'Ω' => 'O', 'Ά' => 'Α', 'Ί' => 'I', 'Ύ' => 'Y', 'Έ' => 'E', 'Ό' => 'O', 'Ή' => 'I', 'Ώ' => 'O', 'Ϊ' => 'I', 'Ϋ' => 'Y',
			//romanian
			'ă' => 'a', 'Ă' => 'A', 'â' => 'a', 'Â' => 'A', 'î' => 'i', 'Î' => 'I', 'ş' => 's', 'Ş' => 'S', 'ţ' => 't', 'Ţ' => 'T',
			// russian
			'А'  => 'A', 'а'  => 'a', 'Б'  => 'B', 'б'  => 'b', 'В'  => 'V', 'в'  => 'v', 'Г'  => 'G', 'г'  => 'g', 'Д'  => 'D', 'д'  => 'd', 'Е'  => 'E', 'е'  => 'e', 'Ё'  => 'Jo', 'ё'  => 'jo', 'Ж'  => 'Zh', 'ж'  => 'zh', 'З'  => 'Z', 'з'  => 'z', 'И'  => 'I', 'и'  => 'i', 'Й'  => 'J', 'й'  => 'j', 'К'  => 'K', 'к'  => 'k', 'Л'  => 'L', 'л'  => 'l', 'М'  => 'M', 'м'  => 'm', 'Н'  => 'N', 'н'  => 'n', 'О'  => 'O', 'о'  => 'o', 'П'  => 'P', 'п'  => 'p', 'Р'  => 'R', 'р'  => 'r', 'С'  => 'S', 'с'  => 's', 'Т'  => 'T', 'т'  => 't', 'У'  => 'U', 'у'  => 'u', 'Ф'  => 'F', 'ф'  => 'f', 'Х'  => 'H', 'х'  => 'h', 'Ц'  => 'C', 'ц'  => 'c', 'Ч'  => 'Ch', 'ч'  => 'ch', 'Ш'  => 'Sh', 'ш'  => 'sh', 'Щ'  => 'Shh', 'щ'  => 'shh', 'Ъ'  => '', 'ъ'  => '', 'Ы'  => 'Y', 'ы'  => 'y', 'Ь'  => '', 'ь'  => '', 'Э'  => 'Je', 'э'  => 'je', 'Ю'  => 'Ju', 'ю'  => 'ju', 'Я'  => 'Ja', 'я'  => 'ja',
			// ukrainian
			'Г'  => 'H', 'г' => 'h', 'И' => 'Y', 'и' => 'y', 'Х' => 'Kh', 'х' => 'kh', 'Ц' => 'Ts', 'ц' => 'ts', 'Щ' => 'Shch', 'щ' => 'shch', 'Ю' => 'Iu', 'ю' => 'iu', 'Я' => 'Ia', 'я' => 'ia',
			//bulgarian
			'Щ'  => 'Sht', 'щ' => 'sht', 'Ъ' => 'a', 'ъ' => 'a',
			//georgian
			'ა'  => 'a', 'ბ' => 'b', 'გ' => 'g', 'დ' => 'd', 'ე' => 'e', 'ვ' => 'v', 'ზ' => 'z', 'თ' => 'th', 'ი' => 'i', 'კ' => 'k', 'ლ' => 'l', 'მ' => 'm', 'ნ' => 'n', 'ო' => 'o', 'პ' => 'p', 'ჟ' => 'zh', 'რ' => 'r', 'ს' => 's', 'ტ' => 't', 'უ' => 'u', 'ფ' => 'ph', 'ქ' => 'q', 'ღ' => 'gh', 'ყ' => 'qh', 'შ' => 'sh', 'ჩ' => 'ch', 'ც' => 'ts', 'ძ' => 'dz', 'წ' => 'ts', 'ჭ' => 'tch', 'ხ' => 'kh', 'ჯ' => 'j', 'ჰ' => 'h',
			// Armenian
			'Ա'  => 'A', 'ա'  => 'a', 'Բ'  => 'B', 'բ'  => 'b', 'Գ'  => 'G', 'գ'  => 'g', 'Դ'  => 'D', 'դ'  => 'd', ' Ե' => ' Ye', 'Ե'  => 'E', ' ե' => ' ye', 'ե'  => 'e', 'Զ'  => 'Z', 'զ'  => 'z', 'Է'  => 'E', 'է'  => 'e', 'Ը'  => 'Y', 'ը'  => 'y', 'Թ'  => 'T', 'թ'  => 't', 'Ժ'  => 'Zh', 'ժ'  => 'zh', 'Ի'  => 'I', 'ի'  => 'i', 'Լ'  => 'L', 'լ'  => 'l', 'Խ'  => 'KH', 'խ'  => 'kh', 'Ծ'  => 'TS', 'ծ'  => 'ts', 'Կ'  => 'K', 'կ'  => 'K', 'Հ'  => 'H', 'հ'  => 'h', 'Ձ'  => 'DZ', 'ձ'  => 'dz', 'Ղ'  => 'GH', 'ղ'  => 'gh', 'Ճ'  => 'J', 'Ճ'  => 'j', 'Մ'  => 'M', 'մ'  => 'm', 'Յ'  => 'Y', 'յ'  => 'y', 'Ն'  => 'N', 'ն'  => 'n', 'Շ'  => 'SH', 'շ'  => 'sh', ' Ո' => 'VO', 'Ո'  => 'VO', ' ո' => ' vo', 'ո'  => 'o', 'Չ'  => 'Ch', 'չ'  => 'ch', 'Պ'  => 'P', 'պ'  => 'p', 'Ջ'  => 'J', 'ջ'  => 'j', 'Ռ'  => 'R', 'ռ'  => 'r', 'Ս'  => 'S', 'ս'  => 's', 'Վ'  => 'V', 'վ'  => 'v', 'Տ'  => 'T', 'տ'  => 't', 'Ր'  => 'R', 'ր'  => 'r', 'Ց'  => 'C', 'ց'  => 'c', 'Ու' => 'U', 'ու' => 'u', 'Փ'  => 'P', 'փ'  => 'p', 'Ք'  => 'Q', 'ք'  => 'q', 'Եվ' => 'EV', 'և'  => 'ev', 'Օ'  => 'O', 'օ'  => 'o', 'Ֆ'  => 'F', 'ֆ'  => 'f',
			// Serbian
			'Ђ'  => 'DJ', 'Ж'  => 'Z', 'З'  => 'Z', 'Љ'  => 'LJ', 'Њ'  => 'NJ', 'Ш'  => 'S', 'Ћ'  => 'C', 'Ц'  => 'C', 'Ч'  => 'C', 'Џ'  => 'DZ', 'ђ'  => 'dj', 'ж'  => 'z', 'з'  => 'z', 'и'  => 'i', 'љ'  => 'lj', 'њ'  => 'nj', 'ш'  => 's', 'ћ'  => 'c', 'ч'  => 'c', 'џ'  => 'dz', 'Ња' => 'Nja', 'Ње' => 'Nje', 'Њи' => 'Nji', 'Њо' => 'Njo', 'Њу' => 'Nju', 'Ља' => 'Lja', 'Ље' => 'Lje', 'Љи' => 'Lji', 'Љо' => 'Ljo', 'Љу' => 'Lju', 'Џа' => 'Dza', 'Џе' => 'Dze', 'Џи' => 'Dzi', 'Џо' => 'Dzo', 'Џу' => 'Dzu',
		];

		if (function_exists('iconv')) {
			$text = iconv('UTF-8', 'UTF-8//TRANSLIT//IGNORE', $text);
		}

		$text = str_replace(array_keys($table), array_values($table), $text);

		return $text;
	}
}

class TransliteratePlugin {
	function transliterate($text) {
		if (! $text) {
			return $text;
		}

		return strtolower(transliterate($text));
	}

	function addTransliterate() {
		//add script on compile
		Event::on('Vvveb\System\Core\View', 'compile', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			//insert js and css on post and product page
			if (($template == 'content' . DS . 'post.html') || ($template == 'product' . DS . 'product.html')) {
				//insert script
				$vTpl->loadTemplateFile(__DIR__ . '/admin/template/content.tpl');
				//$vTpl->addCommand('body|append', $script);
			}

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});
	}

	function admin() {
		$this->addTransliterate();
	}

	function app() {
		//post component
		Event::on('Vvveb\Component\Post', 'results', __CLASS__, function ($results = false) {
			if ($results && isset($results['slug']) && $results['slug']) {
				$transliterateSlug = $this->transliterate($results['slug']);
				$results['url'] = str_replace($results['slug'], $transliterateSlug, $results['url']);
				$results['slug'] = $transliterateSlug;
			}

			return [$results];
		});

		//posts component
		Event::on('Vvveb\Component\Posts', 'results', __CLASS__, function ($results = false) {
			if (isset($results['post'])) {
				foreach ($results['post'] as &$post) {
					if (isset($post['slug']) && $post['slug']) {
						$transliterateSlug = $this->transliterate($post['slug']);
						$post['url'] = str_replace($post['slug'], $transliterateSlug, $post['url']);
						$post['slug'] = $transliterateSlug;
					}
				}
			}

			return [$results];
		});
	}

	function __construct() {
		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$transliteratePlugin = new TransliteratePlugin();

/*


  $greek = [
	'α', 'β', 'γ', 'δ', 'ε', 'ζ', 'η', 'ι', 'κ', 'λ', 'μ', 'ν', 'ξ', 'ο',
	'π', 'ρ', 'σ', 'τ', 'υ', 'φ', 'ω', 'ά', 'ί', 'ύ', 'έ', 'ό', 'ή', 'ώ',
	'ϊ', 'ϋ', 'ΐ', 'ΰ', 'ς',
	'Α', 'Β', 'Γ', 'Δ', 'Ε', 'Ζ', 'Η', 'Ι', 'Κ', 'Λ', 'Μ', 'Ν', 'Ξ', 'Ο',
	'Π', 'Ρ', 'Σ', 'Τ', 'Υ', 'Φ', 'Ω', 'Ά', 'Ί', 'Ύ', 'Έ', 'Ό', 'Ή', 'Ώ',
	'Ϊ', 'Ϋ',
  ];
	$latin = [
		'a', 'v', 'g', 'd', 'e', 'z', 'i', 'i', 'k', 'l', 'm', 'n', 'x', 'o',
		'p', 'r', 's', 't', 'y', 'f', 'o', 'a', 'i', 'y', 'e', 'o', 'i', 'o',
		'i', 'e', 'i', 'y', 's',
		'A', 'V', 'G', 'D', 'E', 'Z', 'I', 'I', 'K', 'L', 'M', 'N', 'X', 'O',
		'P', 'R', 'S', 'T', 'Y', 'F', 'O', 'Α', 'I', 'Y', 'E', 'O', 'I', 'O',
		'I', 'Y',
	];

	$array = array_combine($greek, $latin);
	var_export($array);
*/
