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

namespace Vvveb\Plugins\Seo;

use function Vvveb\getMultiPostContentMeta;
use function Vvveb\getMultiProductContentMeta;
use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

#[\AllowDynamicProperties]
class GraphQLSchema {
	function types($types) {
		$types['og'] =
	  [
	  	'name'       => 'og',
	  	'properties' => [
	  		'title' => [
	  			'name'        => 'title',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'description' => [
	  			'name'        => 'description',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  	],
	  ];
		$types['twitter'] =
	  [
	  	'name'       => 'twitter',
	  	'properties' => [
	  		'title' => [
	  			'name'        => 'title',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'content' => [
	  			'name'        => 'content',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'label1' => [
	  			'name'        => 'label1',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'data1' => [
	  			'name'        => 'data1',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'label2' => [
	  			'name'        => 'label2',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  		'data2' => [
	  			'name'        => 'data2',
	  			'description' => '',
	  			'type'        => 'String',
	  		],
	  	],
	  ];

		$types['seo'] =
	  [
	  	'name'       => 'post_seo',
	  	'properties' => [
	  		'og' => [
	  			'name'        => 'og',
	  			'description' => '',
	  			'type'        => 'OgType',
	  		],
	  		'twitter' => [
	  			'name'        => 'twitter',
	  			'description' => '',
	  			'type'        => 'TwitterType',
	  		],
	  	],
	  ];

		$types['seo']['properties']['seo'] =
	  [
	  	'name' => 'seo',
	  	'type' => '[SeoType]',
	  ];

		$types['post']['properties']['seo'] =
	  [
	  	'name' => 'seo',
	  	'type' => '[SeoType]',
	  ];

		$types['product']['properties']['seo'] =
	  [
	  	'name' => 'seo',
	  	'type' => '[SeoType]',
	  ];

		return [$types];
	}

	function models($models) {
		return [$models];
	}

	function queries($queries) {
		//$queries['seo'] = ;

		return [$queries];
	}

	function mutations($mutations) {
		$mutations['seo'] = $this->seo;

		return [$mutations];
	}

	function __construct() {
		//register schema defintion for types and add seo types for post and product
		Event::on('Vvveb\Controller\Schema', 'types', __CLASS__, [$this, 'types']);
		Event::on('Vvveb\Controller\Schema', 'models', __CLASS__, [$this, 'models']);
	}
}
#[\AllowDynamicProperties]
class GraphQL {
	function seo($parentTypeName, $modelName, $args, $returnIsList, $isConnection) {
		if ($parentTypeName == 'PostType') {
			$seo = [];

			if (isset($args['post_id'])) {
				$seo = getMultiPostContentMeta($args['post_id'], 'seo') ?? [];
			}
		} else {
			if ($parentTypeName == 'ProductType') {
				$seo = [];

				if (isset($args['product_id'])) {
					$seo = getMultiProductContentMeta($args['product_id'], 'seo') ?? [];
				}
			}
		}

		$data = [];

		if ($seo) {
			foreach ($seo as $meta) {
				$data[$meta['key']] = $meta['value'];
			}
		}

		if ($returnIsList) {
			return [$data];
		}

		return $data;
	}

	function types($types) {
		$types['Seo'] = [$this, 'seo'];

		return [$types];
	}

	function queries($queries) {
		//$queries['seo'] = ;

		return [$queries];
	}

	function mutations($mutations) {
		///$mutations['seo'] = $this->seo;
		return [$mutations];
	}

	function __construct() {
		//register plugin queries and mutations
		Event::on('Vvveb\Controller\Index', 'queries', __CLASS__, [$this, 'queries']);
		Event::on('Vvveb\Controller\Index', 'mutations', __CLASS__, [$this, 'mutations']);
		Event::on('Vvveb\Controller\Index', 'types', __CLASS__, [$this, 'types']);
		Event::on('Vvveb\Controller\Index', 'mutations', __CLASS__, [$this, 'mutations']);
	}
}

$graphQLSchema = new GraphQLSchema();
