"""
Utility script to check the progress and status of documentation in Pinecone.
"""
import os
from pinecone import Pinecone
from dotenv import load_dotenv

load_dotenv()

PINECONE_API_KEY = os.environ.get('PINECONE_API_KEY', 'pcsk_339VMc_3fF2iGeefNdKNSionNQC3dmNvzsAJTAft3ZdrZ94UmspP1SaTqNyaQPeYyDj7ui')
PINECONE_INDEX_NAME = os.environ.get('PINECONE_INDEX_NAME', 'quickstart')

def check_pinecone_status():
    """Check Pinecone index status and statistics."""
    try:
        pc = Pinecone(api_key=PINECONE_API_KEY)
        index = pc.Index(PINECONE_INDEX_NAME)
        
        print("=" * 70)
        print("PINECONE INDEX STATUS")
        print("=" * 70)
        print(f"Index Name: {PINECONE_INDEX_NAME}")
        print()
        
        # Get index stats
        stats = index.describe_index_stats()
        
        print("Index Statistics:")
        print(f"  Total Vectors: {stats.get('total_vector_count', 0):,}")
        print(f"  Index Fullness: {stats.get('index_fullness', 0):.2%}")
        
        # Get namespace stats if available
        if 'namespaces' in stats:
            print("\nNamespaces:")
            for ns_name, ns_stats in stats['namespaces'].items():
                print(f"  {ns_name}: {ns_stats.get('vector_count', 0):,} vectors")
        
        # Get dimension info
        index_info = pc.describe_index(PINECONE_INDEX_NAME)
        print(f"\nIndex Configuration:")
        print(f"  Dimension: {index_info.dimension}")
        print(f"  Metric: {index_info.metric}")
        print(f"  Pod Type: {index_info.spec.get('pod', {}).get('pod_type', 'N/A')}")
        
        # Sample a few vectors to see what's stored
        print("\nSampling stored vectors...")
        try:
            sample_results = index.query(
                vector=[0.0] * index_info.dimension,  # Dummy vector
                top_k=5,
                include_metadata=True
            )
            
            if sample_results.get('matches'):
                print(f"  Found {len(sample_results['matches'])} sample vectors:")
                for i, match in enumerate(sample_results['matches'][:3], 1):
                    metadata = match.metadata
                    print(f"    {i}. URL: {metadata.get('url', 'N/A')}")
                    print(f"       Title: {metadata.get('title', 'N/A')[:60]}...")
                    print(f"       Chunk: {metadata.get('chunk_index', 'N/A')}")
            else:
                print("  ⚠️  No vectors found in index")
        except Exception as e:
            print(f"  ⚠️  Could not sample vectors: {str(e)}")
        
        print()
        print("=" * 70)
        print("✅ Index is accessible and ready to use!")
        print("=" * 70)
        
    except Exception as e:
        print("=" * 70)
        print("ERROR CHECKING PINECONE INDEX")
        print("=" * 70)
        print(f"Error: {str(e)}")
        print()
        print("Possible issues:")
        print("  1. Index doesn't exist - Create it first")
        print("  2. API key is incorrect")
        print("  3. Index name is wrong")
        print("  4. Network connectivity issues")
        print("=" * 70)

if __name__ == "__main__":
    check_pinecone_status()

